"""Workspace-aware Nova LSP composition over exact semantic snapshots."""

from __future__ import annotations

import posixpath
import urllib.parse
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .diagnostics import (
    Diagnostic,
    DiagnosticRelatedInformation,
    DiagnosticSnapshot,
)
from .documents import Document, DocumentError
from .nova import NovaFunctionSyntax, NovaImportNameSyntax, NovaImportSyntax, NovaLanguageServer
from .semantic import SemanticError, SemanticSnapshot
from .server import LanguageServer, ServerState
from .source import Span
from .symbols import SymbolError
from .syntax import SyntaxError
from .workspace import WorkspaceDeclaration, WorkspaceIndexError, WorkspaceSymbolIndex
from .workspace_files import (
    ClosedWorkspaceFile,
    LocalWorkspaceMutationEvidence,
    LocalWorkspacePathEvidence,
    WorkspaceUriIdentity,
    local_path_from_file_uri,
    probe_local_workspace_mutation,
    probe_local_workspace_path,
    read_closed_workspace_file,
    scan_closed_workspace_files,
)
from .workspace_folders import (
    WorkspaceFolderError,
    WorkspaceFolderSet,
    WorkspaceFolderSnapshot,
)

_SYMBOL_KINDS = {
    "class": 5,
    "function": 12,
    "variable": 13,
    "parameter": 13,
}
_WORKSPACE_SYMBOL_PARTIAL_CHUNK_SIZE = 16
_CLOSED_WORKSPACE_WATCH_REGISTRATION_ID = (
    "mini-language-server.closed-workspace.didChangeWatchedFiles"
)
_CLOSED_WORKSPACE_WATCH_KIND = 7
_NOVA_IMPORT_DIAGNOSTIC_CODES = frozenset(
    {
        "nova.unresolved-import",
        "nova.import-cycle",
        "nova.duplicate-import-name",
        "nova.duplicate-import-namespace",
        "nova.reserved-import-namespace",
        "nova.unresolved-import-name",
        "nova.ambiguous-import-name",
        "nova.duplicate-export",
        "nova.unresolved-export",
        "nova.unresolved-export-target",
        "nova.ambiguous-export",
        "nova.private-export",
    }
)


@dataclass(frozen=True, slots=True)
class NovaNamespaceBinding:
    """One exact importer-local namespace identity backed by one import binding."""

    imported: NovaImportSyntax
    name: str
    span: Span
    selected: NovaImportNameSyntax | None = None

    @property
    def namespace(self) -> str:
        return self.name

    @property
    def namespace_span(self) -> Span:
        return self.span


class WorkspaceNovaLanguageServer(NovaLanguageServer):
    """Nova server with deterministic, version-safe workspace tooling."""

    def __init__(self) -> None:
        super().__init__()
        self.workspace_symbols = WorkspaceSymbolIndex()
        self.workspace_folders = WorkspaceFolderSet()
        self._workspace_folder_change_support = False
        self._file_create_support = False
        self._file_delete_support = False
        self._file_rename_support = False
        self._file_will_create_support = False
        self._file_will_delete_support = False
        self._file_will_rename_support = False
        self._watched_files_dynamic_registration = False
        self._watched_files_registration_attempted = False
        self._watched_files_registration_request: str | None = None
        self._watched_files_registration_active = False
        self._moniker_support = False
        self._document_link_support = False
        self._closed_workspace_index_initialized = False
        self._closed_workspace_uris: dict[WorkspaceUriIdentity, str] = {}
        self._closed_workspace_base_diagnostics: dict[
            WorkspaceUriIdentity, DiagnosticSnapshot
        ] = {}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            params = message.get("params")
            self._workspace_folder_change_support = (
                self._client_supports_workspace_folders(params)
            )
            self._file_create_support = self._client_supports_file_create(params)
            self._file_delete_support = self._client_supports_file_delete(params)
            self._file_rename_support = self._client_supports_file_rename(params)
            self._file_will_create_support = (
                self._client_supports_file_will_create(params)
            )
            self._file_will_delete_support = (
                self._client_supports_file_will_delete(params)
            )
            self._file_will_rename_support = (
                self._client_supports_file_will_rename(params)
            )
            self._watched_files_dynamic_registration = (
                self._client_supports_watched_files_dynamic_registration(params)
            )
            self._moniker_support = self._client_supports_moniker(params)
            self._document_link_support = self._client_supports_document_links(params)
            try:
                self.workspace_folders.configure(params)
            except WorkspaceFolderError:
                if "id" in message:
                    return self._error(message.get("id"), -32602, "Invalid params")
                return None

        if (
            method == "workspace/didChangeWorkspaceFolders"
            and "id" not in message
            and self.state is ServerState.RUNNING
        ):
            if self._workspace_folder_change_support:
                self._handle_workspace_folder_change(message.get("params"))
            return None

        if (
            method == "workspace/didCreateFiles"
            and "id" not in message
            and self.state is ServerState.RUNNING
        ):
            if self._file_create_support:
                self._handle_workspace_file_index_change(message.get("params"))
            return None

        if (
            method == "workspace/didDeleteFiles"
            and "id" not in message
            and self.state is ServerState.RUNNING
        ):
            if self._file_delete_support:
                self._handle_workspace_file_index_change(message.get("params"))
            return None

        if (
            method == "workspace/didRenameFiles"
            and "id" not in message
            and self.state is ServerState.RUNNING
        ):
            if self._file_rename_support:
                self._handle_workspace_file_renames(message.get("params"))
            return None

        if (
            method == "workspace/didChangeWatchedFiles"
            and "id" not in message
            and self.state is ServerState.RUNNING
        ):
            if self._watched_files_registration_active:
                self._handle_workspace_watched_file_change(message.get("params"))
            return None

        if "id" in message and self.state is ServerState.RUNNING:
            if method == "workspace/willCreateFiles" and self._file_will_create_support:
                return self._handle_workspace_will_create(
                    message.get("id"), message.get("params")
                )
            if method == "workspace/willDeleteFiles" and self._file_will_delete_support:
                return self._handle_workspace_will_delete(
                    message.get("id"), message.get("params")
                )
            if method == "workspace/willRenameFiles" and self._file_will_rename_support:
                return self._handle_workspace_will_rename(
                    message.get("id"), message.get("params")
                )
            if method == "workspace/symbol":
                return self._handle_workspace_symbol(
                    message.get("id"), message.get("params")
                )
            if method == "textDocument/moniker" and self._moniker_support:
                return self._handle_project_function_moniker(
                    message.get("id"), message.get("params")
                )
            if method == "textDocument/documentLink" and self._document_link_support:
                return self._handle_nova_document_links(
                    message.get("id"), message.get("params")
                )
            if method == "textDocument/completion":
                workspace_result = self._handle_workspace_completion(
                    message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result
            if method == "textDocument/hover":
                workspace_result = self._handle_workspace_hover(
                    message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result
            if method in {"textDocument/definition", "textDocument/references"}:
                workspace_result = self._handle_workspace_navigation(
                    method, message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result
            if method == "textDocument/prepareCallHierarchy":
                return self._handle_prepare_call_hierarchy(
                    message.get("id"), message.get("params")
                )
            if method in {"callHierarchy/incomingCalls", "callHierarchy/outgoingCalls"}:
                return self._handle_call_hierarchy_calls(
                    method, message.get("id"), message.get("params")
                )
            if method == "textDocument/prepareRename":
                workspace_result = self._handle_workspace_prepare_rename(
                    message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result
            if method == "textDocument/rename":
                workspace_result = self._handle_workspace_rename(
                    message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result

        result = super().handle(message)
        if (
            method == "initialized"
            and "id" not in message
            and self.state is ServerState.RUNNING
            and not self._closed_workspace_index_initialized
        ):
            self._closed_workspace_index_initialized = True
            self._refresh_closed_workspace_files()
            self._queue_closed_workspace_watch_registration()
        if method == "initialize" and result is not None and "result" in result:
            params = message.get("params")
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                if self._client_supports_workspace_symbol(params):
                    capabilities["workspaceSymbolProvider"] = True
                if self._client_supports_call_hierarchy(params):
                    capabilities["callHierarchyProvider"] = True
                if self._moniker_support:
                    capabilities["monikerProvider"] = True
                if self._document_link_support:
                    capabilities["documentLinkProvider"] = {"resolveProvider": False}
                if self._workspace_folder_change_support:
                    workspace_capabilities = capabilities.setdefault("workspace", {})
                    if isinstance(workspace_capabilities, dict):
                        workspace_capabilities["workspaceFolders"] = {
                            "supported": True,
                            "changeNotifications": True,
                        }
                if self._file_create_support:
                    workspace_capabilities = capabilities.setdefault("workspace", {})
                    if isinstance(workspace_capabilities, dict):
                        file_operations = workspace_capabilities.setdefault(
                            "fileOperations", {}
                        )
                        if isinstance(file_operations, dict):
                            file_operations["didCreate"] = {
                                "filters": [
                                    {
                                        "scheme": "file",
                                        "pattern": {"glob": "**/*.nova"},
                                    }
                                ]
                            }
                if self._file_will_create_support:
                    workspace_capabilities = capabilities.setdefault("workspace", {})
                    if isinstance(workspace_capabilities, dict):
                        file_operations = workspace_capabilities.setdefault(
                            "fileOperations", {}
                        )
                        if isinstance(file_operations, dict):
                            file_operations["willCreate"] = {
                                "filters": [
                                    {
                                        "scheme": "file",
                                        "pattern": {"glob": "**/*.nova"},
                                    }
                                ]
                            }
                if self._file_delete_support:
                    workspace_capabilities = capabilities.setdefault("workspace", {})
                    if isinstance(workspace_capabilities, dict):
                        file_operations = workspace_capabilities.setdefault(
                            "fileOperations", {}
                        )
                        if isinstance(file_operations, dict):
                            file_operations["didDelete"] = {
                                "filters": [
                                    {
                                        "scheme": "file",
                                        "pattern": {"glob": "**/*.nova"},
                                    }
                                ]
                            }
                if self._file_will_delete_support:
                    workspace_capabilities = capabilities.setdefault("workspace", {})
                    if isinstance(workspace_capabilities, dict):
                        file_operations = workspace_capabilities.setdefault(
                            "fileOperations", {}
                        )
                        if isinstance(file_operations, dict):
                            file_operations["willDelete"] = {
                                "filters": [
                                    {
                                        "scheme": "file",
                                        "pattern": {"glob": "**/*.nova"},
                                    }
                                ]
                            }
                if self._file_rename_support:
                    workspace_capabilities = capabilities.setdefault("workspace", {})
                    if isinstance(workspace_capabilities, dict):
                        file_operations = workspace_capabilities.setdefault(
                            "fileOperations", {}
                        )
                        if isinstance(file_operations, dict):
                            file_operations["didRename"] = {
                                "filters": [
                                    {
                                        "scheme": "file",
                                        "pattern": {"glob": "**/*.nova"},
                                    }
                                ]
                            }
                if self._file_will_rename_support:
                    workspace_capabilities = capabilities.setdefault("workspace", {})
                    if isinstance(workspace_capabilities, dict):
                        file_operations = workspace_capabilities.setdefault(
                            "fileOperations", {}
                        )
                        if isinstance(file_operations, dict):
                            file_operations["willRename"] = {
                                "filters": [
                                    {
                                        "scheme": "file",
                                        "pattern": {"glob": "**/*.nova"},
                                    }
                                ]
                            }
        return result

    def _handle_document_notification(self, method: str, params: Any) -> None:
        uri = self._document_uri(params)
        if uri is not None and method == "textDocument/didOpen":
            self._drop_closed_workspace_identity(uri)
        previous = self.workspace_symbols.get(uri) if uri is not None else None
        super()._handle_document_notification(method, params)
        if uri is None:
            return
        if method == "textDocument/didClose":
            if previous is not None:
                with suppress(WorkspaceIndexError):
                    self.workspace_symbols.remove(uri, expected=previous)
            self._restore_closed_workspace_file(uri)
            self._publish_workspace_diagnostics()
            return
        if method not in {"textDocument/didOpen", "textDocument/didChange"}:
            return
        if not self.workspace_folders.contains(uri):
            if previous is not None:
                with suppress(WorkspaceIndexError):
                    self.workspace_symbols.remove(uri, expected=previous)
                self._publish_workspace_diagnostics()
            return
        current = self.semantics.get(uri)
        if current is None:
            return
        try:
            self.workspace_symbols.replace(current, expected=previous)
        except WorkspaceIndexError:
            return
        self._publish_workspace_diagnostics()

    def _handle_workspace_will_create(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        """Preflight one Nova create batch without mutating workspace state."""
        return self._handle_workspace_file_preflight(
            request_id,
            params,
            operation="create",
        )

    def _handle_workspace_will_delete(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        """Preflight one Nova delete batch without mutating workspace state."""
        return self._handle_workspace_file_preflight(
            request_id,
            params,
            operation="delete",
        )

    def _handle_workspace_file_preflight(
        self,
        request_id: Any,
        params: Any,
        *,
        operation: str,
    ) -> dict[str, Any]:
        """Validate one create/delete batch against exact server-known ownership."""
        try:
            uris = self._workspace_file_operation_uris(params)
        except DocumentError:
            return self._error(request_id, -32602, "Invalid params")
        if not uris:
            return self._result(request_id, None)

        requested = tuple(
            (uri, WorkspaceFolderSet.uri_identity(uri))
            for uri in uris
        )
        document_snapshots = self.documents.snapshots()
        open_nova_identities = {
            WorkspaceFolderSet.uri_identity(document.uri)
            for document in document_snapshots
            if document.language_id == self.nova_adapter.language_id
        }
        relevant = tuple(
            (uri, identity)
            for uri, identity in requested
            if (
                identity in open_nova_identities
                or self._workspace_file_affects_closed_index(uri)
            )
        )
        if not relevant:
            return self._result(request_id, None)

        relevant_identities = tuple(identity for _, identity in relevant)
        if len(set(relevant_identities)) != len(relevant_identities):
            return self._error(
                request_id,
                -32803,
                f"File {operation} preflight failed: "
                f"{operation} identities must be unique",
            )

        affected_identities = frozenset(relevant_identities)
        captured_documents = tuple(
            document
            for document in document_snapshots
            if (
                document.language_id == self.nova_adapter.language_id
                and WorkspaceFolderSet.uri_identity(document.uri)
                in affected_identities
            )
        )
        captured_workspace = self.workspace_symbols.snapshots()
        captured_folders = self.workspace_folders.snapshot()
        captured_closed = {
            identity: uri
            for identity, uri in self._closed_workspace_uris.items()
            if identity in affected_identities
        }
        captured_closed_snapshots = tuple(
            snapshot
            for snapshot in captured_workspace
            if (
                identity := WorkspaceFolderSet.uri_identity(snapshot.uri)
            ) in captured_closed
            and captured_closed[identity] == snapshot.uri
        )
        captured_local = {
            identity: evidence
            for uri, identity in relevant
            if (evidence := probe_local_workspace_path(uri)) is not None
        }
        captured_mutation = {
            identity: evidence
            for uri, identity in relevant
            if (evidence := probe_local_workspace_mutation(uri)) is not None
        }

        try:
            context = self.requests.start(request_id)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        validation_error: DocumentError | None = None
        stale_closed_inputs = False
        stale_local_inputs = False
        stale_mutation_inputs = False
        try:
            self.requests.checkpoint(context)
            if not self._closed_workspace_snapshots_current(
                captured_closed_snapshots
            ):
                self._refresh_closed_workspace_files()
                return self._error(request_id, -32801, "Content modified")

            def publish() -> dict[str, Any] | None:
                nonlocal validation_error, stale_closed_inputs
                nonlocal stale_local_inputs, stale_mutation_inputs
                if not self._local_workspace_path_evidence_current(captured_local):
                    stale_local_inputs = True
                    return None
                if not self._local_workspace_mutation_evidence_current(
                    captured_mutation
                ):
                    stale_mutation_inputs = True
                    return None
                if any(
                    self._closed_workspace_uris.get(identity) != uri
                    for identity, uri in captured_closed.items()
                ):
                    stale_closed_inputs = True
                    return None
                try:
                    self._validate_workspace_file_preflight(
                        operation,
                        relevant,
                        captured_documents=captured_documents,
                        captured_closed=captured_closed,
                        captured_local=captured_local,
                        captured_mutation=captured_mutation,
                    )
                except DocumentError as exc:
                    validation_error = exc
                    return None
                if not self._closed_workspace_snapshots_current(
                    captured_closed_snapshots
                ):
                    stale_closed_inputs = True
                    return None
                if not self._local_workspace_path_evidence_current(captured_local):
                    stale_local_inputs = True
                    return None
                if not self._local_workspace_mutation_evidence_current(
                    captured_mutation
                ):
                    stale_mutation_inputs = True
                    return None
                self.requests.checkpoint(context)
                return self._result(request_id, None)

            try:
                response = self.documents.commit_matching_if_current(
                    captured_documents,
                    lambda document: (
                        document.language_id == self.nova_adapter.language_id
                        and WorkspaceFolderSet.uri_identity(document.uri)
                        in affected_identities
                    ),
                    lambda: self.workspace_symbols.commit_snapshots_if_current(
                        captured_workspace,
                        lambda: self.workspace_folders.commit_if_current(
                            captured_folders.generation,
                            publish,
                        ),
                    ),
                )
            except (DocumentError, WorkspaceIndexError, WorkspaceFolderError):
                return self._error(request_id, -32801, "Content modified")

            if stale_closed_inputs or stale_local_inputs or stale_mutation_inputs:
                self._refresh_closed_workspace_files()
                return self._error(request_id, -32801, "Content modified")
            if validation_error is not None:
                return self._error(
                    request_id,
                    -32803,
                    f"File {operation} preflight failed: {validation_error}",
                )
            assert response is not None
            return response
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _validate_workspace_file_preflight(
        operation: str,
        requested: tuple[tuple[str, WorkspaceUriIdentity], ...],
        *,
        captured_documents: tuple[Document, ...],
        captured_closed: dict[WorkspaceUriIdentity, str],
        captured_local: dict[WorkspaceUriIdentity, LocalWorkspacePathEvidence],
        captured_mutation: dict[
            WorkspaceUriIdentity, LocalWorkspaceMutationEvidence
        ],
    ) -> None:
        """Validate operation-specific invariants over one exact capture."""
        if operation not in {"create", "delete"}:
            raise DocumentError(f"unsupported file operation: {operation}")

        for uri, identity in requested:
            mutation = captured_mutation.get(identity)
            if mutation is not None and not mutation.can_mutate_parent:
                raise DocumentError(
                    f"{operation} parent is not writable/searchable: {uri}"
                )

        if operation == "delete":
            return

        open_by_identity = {
            WorkspaceFolderSet.uri_identity(document.uri): document.uri
            for document in captured_documents
        }
        for uri, identity in requested:
            open_uri = open_by_identity.get(identity)
            if open_uri is not None:
                raise DocumentError(f"create target already open: {open_uri}")
            closed_uri = captured_closed.get(identity)
            if closed_uri is not None:
                raise DocumentError(f"create target already indexed: {closed_uri}")
            local = captured_local.get(identity)
            if local is not None and local.exists:
                raise DocumentError(f"create target already exists on disk: {uri}")

    def _handle_workspace_will_rename(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        """Preflight one open + detached Nova rename batch without mutation."""
        try:
            renames = self._workspace_file_rename_pairs(
                params,
                include_closed=True,
            )
        except DocumentError:
            return self._error(request_id, -32602, "Invalid params")
        if not renames:
            return self._result(request_id, None)

        affected_identities = frozenset(
            WorkspaceFolderSet.uri_identity(uri)
            for old_uri, new_uri in renames
            for uri in (old_uri, new_uri)
        )
        captured_workspace = self.workspace_symbols.snapshots()
        try:
            import_changes = self._nova_import_rename_changes(
                captured_workspace,
                renames,
            )
        except DocumentError as exc:
            return self._error(
                request_id,
                -32803,
                f"File rename preflight failed: {exc}",
            )
        guarded_identities = affected_identities | frozenset(
            WorkspaceFolderSet.uri_identity(uri)
            for uri in import_changes
        )
        captured_document_by_identity = {
            WorkspaceFolderSet.uri_identity(document.uri): document
            for document in self.documents.snapshots()
            if WorkspaceFolderSet.uri_identity(document.uri) in affected_identities
        }
        rewrite_identities = {
            WorkspaceFolderSet.uri_identity(uri)
            for uri in import_changes
        }
        for snapshot in captured_workspace:
            identity = WorkspaceFolderSet.uri_identity(snapshot.uri)
            if identity not in rewrite_identities:
                continue
            if self.documents.get(snapshot.uri) is not None:
                captured_document_by_identity[identity] = (
                    snapshot.symbols.syntax.document
                )
        captured_documents = tuple(
            captured_document_by_identity[identity]
            for identity in sorted(captured_document_by_identity)
        )
        captured_folders = self.workspace_folders.snapshot()
        captured_closed = {
            identity: uri
            for identity, uri in self._closed_workspace_uris.items()
            if identity in guarded_identities
        }
        captured_closed_snapshots = tuple(
            snapshot
            for snapshot in captured_workspace
            if (
                identity := WorkspaceFolderSet.uri_identity(snapshot.uri)
            ) in captured_closed
            and captured_closed[identity] == snapshot.uri
        )
        captured_local: dict[
            WorkspaceUriIdentity, LocalWorkspacePathEvidence
        ] = {}
        captured_mutation: dict[
            WorkspaceUriIdentity, LocalWorkspaceMutationEvidence
        ] = {}
        for old_uri, new_uri in renames:
            for uri in (old_uri, new_uri):
                identity = WorkspaceFolderSet.uri_identity(uri)
                if identity not in captured_local:
                    evidence = probe_local_workspace_path(uri)
                    if evidence is not None:
                        captured_local[identity] = evidence
                if identity not in captured_mutation:
                    mutation = probe_local_workspace_mutation(uri)
                    if mutation is not None:
                        captured_mutation[identity] = mutation

        try:
            context = self.requests.start(request_id)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        validation_error: DocumentError | None = None
        stale_closed_inputs = False
        stale_local_inputs = False
        stale_mutation_inputs = False
        try:
            self.requests.checkpoint(context)
            if not self._closed_workspace_snapshots_current(
                captured_closed_snapshots
            ):
                self._refresh_closed_workspace_files()
                return self._error(request_id, -32801, "Content modified")

            def publish() -> dict[str, Any] | None:
                nonlocal validation_error, stale_closed_inputs
                nonlocal stale_local_inputs, stale_mutation_inputs
                if not self._local_workspace_path_evidence_current(captured_local):
                    stale_local_inputs = True
                    return None
                if not self._local_workspace_mutation_evidence_current(
                    captured_mutation
                ):
                    stale_mutation_inputs = True
                    return None
                if any(
                    self._closed_workspace_uris.get(identity) != uri
                    for identity, uri in captured_closed.items()
                ):
                    stale_closed_inputs = True
                    return None
                try:
                    self.documents.validate_renames(renames)
                    self._validate_closed_workspace_rename_preflight(
                        renames,
                        captured_documents=captured_documents,
                        captured_closed=captured_closed,
                        captured_local=captured_local,
                        captured_mutation=captured_mutation,
                    )
                except DocumentError as exc:
                    validation_error = exc
                    return None

                if not self._closed_workspace_snapshots_current(
                    captured_closed_snapshots
                ):
                    stale_closed_inputs = True
                    return None
                if not self._local_workspace_path_evidence_current(captured_local):
                    stale_local_inputs = True
                    return None
                if not self._local_workspace_mutation_evidence_current(
                    captured_mutation
                ):
                    stale_mutation_inputs = True
                    return None
                self.requests.checkpoint(context)
                if not import_changes:
                    return self._result(request_id, None)
                versions = self._workspace_edit_versions(tuple(captured_workspace))
                workspace_edit = self._workspace_edit(
                    import_changes,
                    versions=versions,
                    annotation_label="Update Nova imports for file rename",
                )
                return self._result(request_id, workspace_edit)

            try:
                response = self.documents.commit_matching_if_current(
                    captured_documents,
                    lambda document: (
                        WorkspaceFolderSet.uri_identity(document.uri)
                        in guarded_identities
                    ),
                    lambda: self.workspace_symbols.commit_snapshots_if_current(
                        captured_workspace,
                        lambda: self.workspace_folders.commit_if_current(
                            captured_folders.generation,
                            publish,
                        ),
                    ),
                )
            except (DocumentError, WorkspaceIndexError, WorkspaceFolderError):
                return self._error(request_id, -32801, "Content modified")

            if stale_closed_inputs or stale_local_inputs or stale_mutation_inputs:
                self._refresh_closed_workspace_files()
                return self._error(request_id, -32801, "Content modified")
            if validation_error is not None:
                return self._error(
                    request_id,
                    -32803,
                    f"File rename preflight failed: {validation_error}",
                )
            assert response is not None
            return response
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        finally:
            self.requests.finish(context)

    def _validate_closed_workspace_rename_preflight(
        self,
        renames: tuple[tuple[str, str], ...],
        *,
        captured_documents: tuple[Document, ...],
        captured_closed: dict[WorkspaceUriIdentity, str],
        captured_local: dict[WorkspaceUriIdentity, LocalWorkspacePathEvidence],
        captured_mutation: dict[
            WorkspaceUriIdentity, LocalWorkspaceMutationEvidence
        ],
    ) -> None:
        """Validate canonical open/detached ownership for one rename batch."""
        source_identities = tuple(
            WorkspaceFolderSet.uri_identity(old_uri)
            for old_uri, _ in renames
        )
        destination_identities = tuple(
            WorkspaceFolderSet.uri_identity(new_uri)
            for _, new_uri in renames
        )
        if len(set(source_identities)) != len(source_identities):
            raise DocumentError("rename source identities must be unique")
        if len(set(destination_identities)) != len(destination_identities):
            raise DocumentError("rename destination identities must be unique")

        moving_sources = set(source_identities)
        for old_uri, source_identity in zip(
            (old_uri for old_uri, _ in renames),
            source_identities,
            strict=True,
        ):
            mutation = captured_mutation.get(source_identity)
            if mutation is not None and not mutation.can_mutate_parent:
                raise DocumentError(
                    f"rename source parent is not writable/searchable: {old_uri}"
                )
        for new_uri, destination_identity in zip(
            (new_uri for _, new_uri in renames),
            destination_identities,
            strict=True,
        ):
            mutation = captured_mutation.get(destination_identity)
            if mutation is not None and not mutation.can_mutate_parent:
                raise DocumentError(
                    f"rename destination parent is not writable/searchable: {new_uri}"
                )

        open_by_identity = {
            WorkspaceFolderSet.uri_identity(document.uri): document.uri
            for document in captured_documents
        }
        for new_uri, destination_identity in zip(
            (new_uri for _, new_uri in renames),
            destination_identities,
            strict=True,
        ):
            open_uri = open_by_identity.get(destination_identity)
            if (
                open_uri is not None
                and destination_identity not in moving_sources
            ):
                raise DocumentError(
                    f"rename destination already open: {new_uri}"
                )
            closed_uri = captured_closed.get(destination_identity)
            if (
                closed_uri is not None
                and destination_identity not in moving_sources
            ):
                raise DocumentError(
                    f"rename destination already indexed: {closed_uri}"
                )
            local = captured_local.get(destination_identity)
            if (
                local is not None
                and local.exists
                and destination_identity not in moving_sources
            ):
                raise DocumentError(
                    f"rename destination already exists on disk: {new_uri}"
                )

    @staticmethod
    def _local_workspace_path_evidence_current(
        captured: dict[WorkspaceUriIdentity, LocalWorkspacePathEvidence],
    ) -> bool:
        """Return whether every captured local path entry is byte-identity agnostic current."""
        return all(
            probe_local_workspace_path(evidence.uri) == evidence
            for evidence in captured.values()
        )

    @staticmethod
    def _local_workspace_mutation_evidence_current(
        captured: dict[WorkspaceUriIdentity, LocalWorkspaceMutationEvidence],
    ) -> bool:
        """Return whether captured local mutation prerequisites remain exact-current."""
        return all(
            probe_local_workspace_mutation(evidence.uri) == evidence
            for evidence in captured.values()
        )

    def _handle_workspace_watched_file_change(self, params: Any) -> None:
        """Reconcile detached Nova files after one validated watcher batch."""
        try:
            changes = self._workspace_watched_file_changes(params)
        except DocumentError:
            return
        if not any(
            self._workspace_file_affects_closed_index(uri)
            for uri, _ in changes
        ):
            return
        self._refresh_closed_workspace_files()

    @staticmethod
    def _workspace_watched_file_changes(
        params: Any,
    ) -> tuple[tuple[str, int], ...]:
        if not isinstance(params, dict):
            raise DocumentError("watched-file params must be an object")
        changes = params.get("changes")
        if not isinstance(changes, list):
            raise DocumentError("watched-file params must contain changes")

        parsed: list[tuple[str, int]] = []
        for item in changes:
            if not isinstance(item, dict):
                raise DocumentError("watched-file entries must be objects")
            uri = item.get("uri")
            change_type = item.get("type")
            if (
                not isinstance(uri, str)
                or not uri
                or isinstance(change_type, bool)
                or change_type not in {1, 2, 3}
            ):
                raise DocumentError(
                    "watched-file entries require uri and create/change/delete type"
                )
            parsed.append((uri, change_type))
        return tuple(parsed)

    @staticmethod
    def _client_supports_watched_files_dynamic_registration(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        watched = workspace.get("didChangeWatchedFiles")
        return (
            isinstance(watched, dict)
            and watched.get("dynamicRegistration") is True
        )

    def _queue_closed_workspace_watch_registration(self) -> None:
        if (
            not self._watched_files_dynamic_registration
            or self._watched_files_registration_attempted
            or not self.workspace_folders.folders()
        ):
            return
        self._watched_files_registration_attempted = True

        def own_registration(request_id: str) -> None:
            self._watched_files_registration_request = request_id

        self._queue_server_request(
            "client/registerCapability",
            {
                "registrations": [
                    {
                        "id": _CLOSED_WORKSPACE_WATCH_REGISTRATION_ID,
                        "method": "workspace/didChangeWatchedFiles",
                        "registerOptions": {
                            "watchers": [
                                {
                                    "globPattern": "**/*.nova",
                                    "kind": _CLOSED_WORKSPACE_WATCH_KIND,
                                }
                            ]
                        },
                    }
                ]
            },
            on_queued=own_registration,
        )

    def _server_request_cancelled(self, request_id: str, method: str) -> None:
        super()._server_request_cancelled(request_id, method)
        if (
            method == "client/registerCapability"
            and request_id == self._watched_files_registration_request
        ):
            self._watched_files_registration_request = None

    def _server_request_completed(
        self,
        request_id: str,
        method: str,
        *,
        result: Any,
        error: dict[str, Any] | None,
    ) -> None:
        super()._server_request_completed(
            request_id,
            method,
            result=result,
            error=error,
        )
        if (
            method != "client/registerCapability"
            or request_id != self._watched_files_registration_request
        ):
            return
        self._watched_files_registration_request = None
        self._watched_files_registration_active = (
            error is None and result is None
        )

    def _handle_workspace_file_index_change(self, params: Any) -> None:
        """Reconcile detached Nova files after negotiated create/delete notifications."""
        try:
            uris = self._workspace_file_operation_uris(params)
        except DocumentError:
            return
        if not any(self._workspace_file_affects_closed_index(uri) for uri in uris):
            return
        self._refresh_closed_workspace_files()

    @staticmethod
    def _workspace_file_operation_uris(params: Any) -> tuple[str, ...]:
        if not isinstance(params, dict):
            raise DocumentError("file operation params must be an object")
        files = params.get("files")
        if not isinstance(files, list):
            raise DocumentError("file operation params must contain files")

        uris: list[str] = []
        for item in files:
            if not isinstance(item, dict):
                raise DocumentError("file operation entries must be objects")
            uri = item.get("uri")
            if not isinstance(uri, str) or not uri:
                raise DocumentError("file operation entries require uri")
            uris.append(uri)
        return tuple(uris)

    def _workspace_file_affects_closed_index(self, uri: str) -> bool:
        if not self.workspace_folders.scoped or not self.workspace_folders.contains(uri):
            return False
        path = local_path_from_file_uri(uri)
        return path is not None and path.suffix == ".nova"

    def _workspace_file_rename_pairs(
        self,
        params: Any,
        *,
        include_closed: bool = False,
    ) -> tuple[tuple[str, str], ...]:
        if not isinstance(params, dict):
            raise DocumentError("file rename params must be an object")
        files = params.get("files")
        if not isinstance(files, list):
            raise DocumentError("file rename params must contain files")

        renames: list[tuple[str, str]] = []
        for item in files:
            if not isinstance(item, dict):
                raise DocumentError("file rename entries must be objects")
            old_uri = item.get("oldUri")
            new_uri = item.get("newUri")
            if (
                not isinstance(old_uri, str)
                or not old_uri
                or not isinstance(new_uri, str)
                or not new_uri
            ):
                raise DocumentError("file rename entries require oldUri and newUri")
            document = self.documents.get(old_uri)
            if (
                document is not None
                and document.language_id == self.nova_adapter.language_id
            ):
                renames.append((old_uri, new_uri))
                continue
            if include_closed and (
                self._workspace_file_affects_closed_index(old_uri)
                or self._workspace_file_affects_closed_index(new_uri)
            ):
                renames.append((old_uri, new_uri))
        return tuple(renames)

    def _handle_workspace_file_renames(self, params: Any) -> None:
        """Rekey open Nova documents after one negotiated workspace file rename batch."""
        try:
            renames = self._workspace_file_rename_pairs(params)
        except DocumentError:
            return
        if not renames:
            self._refresh_closed_workspace_files()
            return

        before = self.workspace_symbols.snapshots()
        previous_workspace = {
            old_uri: self.workspace_symbols.get(old_uri)
            for old_uri, _ in renames
        }
        try:
            moved = self.documents.rename_many(renames)
        except DocumentError:
            return
        if not moved:
            self._refresh_closed_workspace_files()
            return

        for previous, current in moved:
            indexed = previous_workspace.get(previous.uri)
            if indexed is not None:
                self.workspace_symbols.remove(previous.uri, expected=indexed)
            self.diagnostics.discard(previous.uri)
            self.semantics.discard(previous.uri)
            self.symbols.discard(previous.uri)
            self.syntax.discard(previous.uri)
            self._document_uri_renamed(previous.uri, current.uri)
            self._queue_publish_diagnostics(previous.uri, None, [])

        for _, document in moved:
            try:
                semantic = self.nova_adapter.publish(self, document)
            except SyntaxError:
                continue
            if not self.workspace_folders.contains(document.uri):
                continue
            self.workspace_symbols.replace(
                semantic,
                expected=self.workspace_symbols.get(document.uri),
            )

        self._sync_closed_workspace_files()
        self._publish_workspace_diagnostics()
        after = self.workspace_symbols.snapshots()
        if before.generation != after.generation:
            self._workspace_scope_changed(before, after)

    def _handle_workspace_folder_change(self, params: Any) -> None:
        before = self.workspace_symbols.snapshots()
        before_folders = self.workspace_folders.folders()
        try:
            changed = self.workspace_folders.apply_change(params)
        except WorkspaceFolderError:
            return
        if not changed:
            return

        self.workspace_symbols.invalidate_complete_queries()

        for snapshot in tuple(before):
            if self.workspace_folders.contains(snapshot.uri):
                continue
            document = self.documents.get(snapshot.uri)
            if (
                document is not None
                and document.language_id == self.nova_adapter.language_id
            ):
                with suppress(SyntaxError, SymbolError, SemanticError):
                    self.nova_adapter.publish(self, document)
            with suppress(WorkspaceIndexError):
                self.workspace_symbols.remove(snapshot.uri, expected=snapshot)

        for document in self.documents.snapshots():
            if (
                document.language_id != self.nova_adapter.language_id
                or not self.workspace_folders.contains(document.uri)
            ):
                continue
            semantic = self.semantics.get(document.uri)
            if semantic is None or semantic.symbols.syntax.document is not document:
                continue
            current = self.workspace_symbols.get(document.uri)
            if current is semantic:
                continue
            with suppress(WorkspaceIndexError):
                self.workspace_symbols.replace(semantic, expected=current)

        self._sync_closed_workspace_files()
        self._publish_workspace_diagnostics()
        after = self.workspace_symbols.snapshots()
        self._workspace_folder_scope_changed(
            before_folders,
            self.workspace_folders.folders(),
        )
        if before.generation != after.generation:
            self._workspace_scope_changed(before, after)

    def _refresh_closed_workspace_files(self) -> None:
        """Rescan bounded local closed Nova files as one workspace transition."""
        before = self.workspace_symbols.snapshots()
        if not self._sync_closed_workspace_files():
            return
        self._publish_workspace_diagnostics()
        after = self.workspace_symbols.snapshots()
        if before.generation != after.generation:
            self._workspace_scope_changed(before, after)

    def _sync_closed_workspace_files(self) -> bool:
        """Reconcile local closed-file snapshots without displacing open buffers."""
        if not self.workspace_folders.scoped:
            return False

        open_identities = frozenset(
            WorkspaceFolderSet.uri_identity(document.uri)
            for document in self.documents.snapshots()
        )
        files = scan_closed_workspace_files(
            tuple(folder.uri for folder in self.workspace_folders.folders()),
            exclude_identities=open_identities,
        )
        discovered = {item.identity: item for item in files}
        changed = False

        for identity, tracked_uri in tuple(self._closed_workspace_uris.items()):
            item = discovered.get(identity)
            if item is not None and item.uri == tracked_uri:
                continue
            current = self.workspace_symbols.get(tracked_uri)
            if current is not None:
                with suppress(WorkspaceIndexError):
                    removed = self.workspace_symbols.remove(
                        tracked_uri,
                        expected=current,
                    )
                    changed = changed or removed is not None
            self._closed_workspace_uris.pop(identity, None)
            self._closed_workspace_base_diagnostics.pop(identity, None)

        for identity in sorted(discovered):
            item = discovered[identity]
            current = self.workspace_symbols.get(item.uri)
            if (
                self._closed_workspace_uris.get(identity) == item.uri
                and current is not None
                and current.symbols.syntax.document.text == item.text
            ):
                continue

            semantic, base_diagnostics = self._detached_nova_snapshot(item)
            try:
                self.workspace_symbols.replace(semantic, expected=current)
            except WorkspaceIndexError:
                continue
            self._closed_workspace_uris[identity] = item.uri
            self._closed_workspace_base_diagnostics[identity] = base_diagnostics
            changed = True
        return changed

    def _drop_closed_workspace_identity(self, uri: str) -> bool:
        """Remove a detached contribution before an editor buffer owns its identity."""
        identity = WorkspaceFolderSet.uri_identity(uri)
        tracked_uri = self._closed_workspace_uris.pop(identity, None)
        self._closed_workspace_base_diagnostics.pop(identity, None)
        if tracked_uri is None:
            return False
        current = self.workspace_symbols.get(tracked_uri)
        if current is None:
            return False
        try:
            return (
                self.workspace_symbols.remove(tracked_uri, expected=current)
                is not None
            )
        except WorkspaceIndexError:
            return False

    def _restore_closed_workspace_file(self, uri: str) -> bool:
        """Restore disk content after the last open buffer relinquishes one URI."""
        if not self.workspace_folders.scoped:
            return False
        identity = WorkspaceFolderSet.uri_identity(uri)
        if any(
            WorkspaceFolderSet.uri_identity(document.uri) == identity
            for document in self.documents.snapshots()
        ):
            return False
        if not self.workspace_folders.contains(uri):
            return False

        item = read_closed_workspace_file(uri)
        if item is None or not self.workspace_folders.contains(item.uri):
            return False
        current = self.workspace_symbols.get(item.uri)
        semantic, base_diagnostics = self._detached_nova_snapshot(item)
        try:
            self.workspace_symbols.replace(semantic, expected=current)
        except WorkspaceIndexError:
            return False
        self._closed_workspace_uris[item.identity] = item.uri
        self._closed_workspace_base_diagnostics[item.identity] = base_diagnostics
        return True

    def _detached_nova_snapshot(
        self, item: ClosedWorkspaceFile
    ) -> tuple[SemanticSnapshot, DiagnosticSnapshot]:
        """Build one read-only Nova semantic + base diagnostic snapshot."""
        detached = LanguageServer()
        document = detached.documents.open(
            uri=item.uri,
            language_id=self.nova_adapter.language_id,
            version=0,
            text=item.text,
        )
        semantic = self.nova_adapter.publish(detached, document)
        diagnostics = detached.diagnostics.get(item.uri)
        if diagnostics is None or diagnostics.semantic is not semantic:
            raise SemanticError("detached Nova diagnostics failed to publish")
        return semantic, diagnostics

    def _nova_import_target_uri(self, importer_uri: str, path: str) -> str | None:
        """Resolve one bounded relative or workspace-root Nova file import."""
        if not path.endswith(".nova"):
            return None
        try:
            importer = urllib.parse.urlsplit(importer_uri)
        except ValueError:
            return None
        if (
            importer.scheme.lower() != "file"
            or importer.query
            or importer.fragment
        ):
            return None

        if path.startswith("@/"):
            relative = path[2:]
            if not relative:
                return None
            if any(
                urllib.parse.unquote(segment) in {".", ".."}
                for segment in relative.split("/")
            ):
                return None
            folder_uri = self.workspace_folders.scope_uri_for(importer_uri)
            if folder_uri is None:
                return None
            try:
                folder = urllib.parse.urlsplit(folder_uri)
                target_uri = urllib.parse.urljoin(
                    folder_uri.rstrip("/") + "/",
                    relative,
                )
                target = urllib.parse.urlsplit(target_uri)
            except ValueError:
                return None
            if (
                folder.scheme.lower() != "file"
                or folder.query
                or folder.fragment
                or target.scheme.lower() != "file"
                or target.query
                or target.fragment
                or not WorkspaceFolderSet._contains(folder_uri, target_uri)
            ):
                return None
        elif path.startswith("./") or path.startswith("../"):
            try:
                target_uri = urllib.parse.urljoin(importer_uri, path)
                target = urllib.parse.urlsplit(target_uri)
            except ValueError:
                return None
            if (
                target.scheme.lower() != "file"
                or target.query
                or target.fragment
            ):
                return None
        else:
            return None

        importer_identity = WorkspaceFolderSet.uri_identity(importer_uri)
        target_identity = WorkspaceFolderSet.uri_identity(target_uri)
        if importer_identity[:2] != target_identity[:2]:
            return None
        return target_uri

    def _nova_exported_namespace_targets(
        self,
        snapshot: SemanticSnapshot,
        snapshots: tuple[SemanticSnapshot, ...],
        *,
        visiting: frozenset[Any] = frozenset(),
    ) -> dict[str, SemanticSnapshot]:
        """Resolve explicit namespace-object exports to canonical target snapshots."""
        tree = snapshot.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax) or not tree.has_export_list:
            return {}

        identity = WorkspaceFolderSet.uri_identity(snapshot.uri)
        if identity in visiting:
            return {}
        next_visiting = visiting | {identity}

        indexed = {
            WorkspaceFolderSet.uri_identity(candidate.uri): candidate
            for candidate in snapshots
        }
        export_counts: dict[str, int] = {}
        for exported in tree.exports:
            export_counts[exported.name] = export_counts.get(exported.name, 0) + 1

        binding_counts: dict[str, int] = {}
        for imported in tree.imports:
            if imported.namespace is not None:
                binding_counts[imported.namespace] = (
                    binding_counts.get(imported.namespace, 0) + 1
                )
            if imported.has_name_list:
                for selected in imported.names:
                    binding_counts[selected.binding_name] = (
                        binding_counts.get(selected.binding_name, 0) + 1
                    )

        candidates: dict[str, list[SemanticSnapshot]] = {}
        for imported in tree.imports:
            target_uri = self._nova_import_target_uri(snapshot.uri, imported.path)
            if target_uri is None:
                continue
            target = indexed.get(WorkspaceFolderSet.uri_identity(target_uri))
            if target is None:
                continue

            namespace = imported.namespace
            if (
                namespace is not None
                and imported.namespace_span is not None
                and namespace not in {"Int", "UInt"}
                and export_counts.get(namespace) == 1
                and binding_counts.get(namespace) == 1
            ):
                candidates.setdefault(namespace, []).append(target)

            if not imported.has_name_list:
                continue
            target_namespaces = self._nova_exported_namespace_targets(
                target,
                snapshots,
                visiting=next_visiting,
            )
            for selected in imported.names:
                binding_name = selected.binding_name
                namespace_target = target_namespaces.get(selected.name)
                if (
                    namespace_target is None
                    or binding_name in {"Int", "UInt"}
                    or export_counts.get(binding_name) != 1
                    or binding_counts.get(binding_name) != 1
                ):
                    continue
                candidates.setdefault(binding_name, []).append(namespace_target)

        return {
            name: values[0]
            for name, values in candidates.items()
            if len(values) == 1
        }

    def _nova_visible_function_map(
        self,
        importer: SemanticSnapshot,
        snapshots: tuple[SemanticSnapshot, ...],
        *,
        legacy_global: bool = True,
        respect_root_exports: bool = False,
    ) -> dict[str, tuple[WorkspaceDeclaration, ...]]:
        """Return deterministic function visibility for one exact importer snapshot.

        Files without explicit imports retain the legacy workspace-global function
        namespace except that foreign private declarations are hidden. Once a file
        declares imports, same-file functions take precedence by name and otherwise
        only exported functions reachable through the explicit import graph are
        visible. Traversal follows real import edges, deduplicates canonical workspace
        identities, and terminates safely on cycles.
        """
        tree = importer.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return {}

        def private_spans(snapshot: SemanticSnapshot) -> frozenset[Span]:
            snapshot_tree = snapshot.symbols.syntax.tree
            if not isinstance(snapshot_tree, NovaFunctionSyntax):
                return frozenset()
            return frozenset(snapshot_tree.private_declarations)

        def declaration_map(
            visible_snapshots: tuple[SemanticSnapshot, ...],
            *,
            include_private_for: SemanticSnapshot | None = None,
        ) -> dict[str, tuple[WorkspaceDeclaration, ...]]:
            grouped: dict[str, list[WorkspaceDeclaration]] = {}
            for snapshot in visible_snapshots:
                hidden = private_spans(snapshot)
                for symbol in snapshot.symbols.symbols:
                    if symbol.kind != "function":
                        continue
                    if (
                        symbol.span in hidden
                        and snapshot is not include_private_for
                    ):
                        continue
                    grouped.setdefault(symbol.name, []).append(
                        WorkspaceDeclaration(snapshot.uri, snapshot, symbol)
                    )
            return {
                name: tuple(
                    sorted(
                        declarations,
                        key=lambda item: (
                            item.uri,
                            item.symbol.span.start,
                            item.symbol.span.end,
                        ),
                    )
                )
                for name, declarations in grouped.items()
            }

        if not tree.imports and legacy_global:
            return declaration_map(
                tuple(snapshots),
                include_private_for=importer,
            )

        indexed = {
            WorkspaceFolderSet.uri_identity(snapshot.uri): snapshot
            for snapshot in snapshots
        }

        def merge_maps(
            maps: tuple[dict[str, tuple[WorkspaceDeclaration, ...]], ...],
        ) -> dict[str, tuple[WorkspaceDeclaration, ...]]:
            grouped: dict[str, list[WorkspaceDeclaration]] = {}
            for visible in maps:
                for name, declarations in visible.items():
                    bucket = grouped.setdefault(name, [])
                    for declaration in declarations:
                        if any(
                            existing.snapshot is declaration.snapshot
                            and existing.symbol is declaration.symbol
                            for existing in bucket
                        ):
                            continue
                        bucket.append(declaration)
            return {
                name: tuple(
                    sorted(
                        declarations,
                        key=lambda item: (
                            item.uri,
                            item.symbol.span.start,
                            item.symbol.span.end,
                        ),
                    )
                )
                for name, declarations in grouped.items()
            }

        def apply_export_list(
            snapshot: SemanticSnapshot,
            visible: dict[str, tuple[WorkspaceDeclaration, ...]],
            *,
            apply: bool,
            namespace_members: frozenset[str] = frozenset(),
        ) -> dict[str, tuple[WorkspaceDeclaration, ...]]:
            if not apply:
                return visible
            snapshot_tree = snapshot.symbols.syntax.tree
            if (
                not isinstance(snapshot_tree, NovaFunctionSyntax)
                or not snapshot_tree.has_export_list
            ):
                return visible
            allowed = {item.name for item in snapshot_tree.exports}
            allowed.update(namespace_members)
            return {
                name: declarations
                for name, declarations in visible.items()
                if name in allowed
            }

        def exported(
            snapshot: SemanticSnapshot,
            visiting: frozenset[Any],
            *,
            include_private_local: bool = False,
        ) -> dict[str, tuple[WorkspaceDeclaration, ...]]:
            identity = WorkspaceFolderSet.uri_identity(snapshot.uri)
            local_all = declaration_map(
                (snapshot,),
                include_private_for=snapshot,
            )
            local_exported = (
                local_all
                if include_private_local
                else declaration_map((snapshot,))
            )
            apply_exports = not include_private_local or respect_root_exports
            if identity in visiting:
                return apply_export_list(
                    snapshot,
                    local_exported,
                    apply=apply_exports,
                )

            snapshot_tree = snapshot.symbols.syntax.tree
            if not isinstance(snapshot_tree, NovaFunctionSyntax):
                return local_exported
            if not snapshot_tree.imports:
                return apply_export_list(
                    snapshot,
                    local_exported,
                    apply=apply_exports,
                )

            next_visiting = visiting | {identity}
            imported_maps: list[dict[str, tuple[WorkspaceDeclaration, ...]]] = []
            namespace_targets: list[
                tuple[str, dict[str, tuple[WorkspaceDeclaration, ...]]]
            ] = []
            namespace_counts: dict[str, int] = {}
            binding_counts: dict[str, int] = {}
            exported_names = {item.name for item in snapshot_tree.exports}
            for imported_item in snapshot_tree.imports:
                if imported_item.namespace is not None:
                    namespace_counts[imported_item.namespace] = (
                        namespace_counts.get(imported_item.namespace, 0) + 1
                    )
                    binding_counts[imported_item.namespace] = (
                        binding_counts.get(imported_item.namespace, 0) + 1
                    )
                if imported_item.has_name_list:
                    for selected in imported_item.names:
                        binding_counts[selected.binding_name] = (
                            binding_counts.get(selected.binding_name, 0) + 1
                        )
            for item in snapshot_tree.imports:
                target_uri = self._nova_import_target_uri(snapshot.uri, item.path)
                if target_uri is None:
                    continue
                target = indexed.get(WorkspaceFolderSet.uri_identity(target_uri))
                if target is None:
                    continue
                target_visible = exported(target, next_visiting)
                if item.namespace is not None:
                    if (
                        item.namespace in {"Int", "UInt"}
                        or namespace_counts.get(item.namespace) != 1
                        or binding_counts.get(item.namespace) != 1
                    ):
                        continue
                    if include_private_local and not respect_root_exports:
                        imported_maps.append(
                            {
                                f"{item.namespace}::{name}": declarations
                                for name, declarations in target_visible.items()
                            }
                        )
                    else:
                        namespace_targets.append((item.namespace, target_visible))
                    continue
                if item.has_name_list:
                    namespace_exports = self._nova_exported_namespace_targets(
                        target,
                        snapshots,
                    )
                    namespace_selected = {
                        selected.name
                        for selected in item.names
                        if selected.name in namespace_exports
                    }
                    target_identity = WorkspaceFolderSet.uri_identity(target.uri)
                    for selected in item.names:
                        namespace_target = namespace_exports.get(selected.name)
                        if (
                            namespace_target is None
                            or selected.name in target_visible
                            or selected.binding_name in {"Int", "UInt"}
                            or binding_counts.get(selected.binding_name) != 1
                        ):
                            continue
                        namespace_visible = exported(
                            namespace_target,
                            next_visiting | {target_identity},
                        )
                        if include_private_local and not respect_root_exports:
                            imported_maps.append(
                                {
                                    f"{selected.binding_name}::{name}": declarations
                                    for name, declarations in namespace_visible.items()
                                }
                            )
                        else:
                            namespace_targets.append(
                                (selected.binding_name, namespace_visible)
                            )
                    if any(selected.alias is not None for selected in item.names):
                        target_visible = merge_maps(
                            tuple(
                                {
                                    selected.binding_name: target_visible[selected.name]
                                }
                                for selected in item.names
                                if selected.name in target_visible
                                and selected.name not in namespace_selected
                            )
                        )
                    else:
                        selected_names = {
                            selected.name
                            for selected in item.names
                            if selected.name not in namespace_selected
                        }
                        target_visible = {
                            name: declarations
                            for name, declarations in target_visible.items()
                            if name in selected_names
                        }
                imported_maps.append(target_visible)

            imported = merge_maps(tuple(imported_maps))
            for name in local_all:
                imported.pop(name, None)
            imported.update(local_exported)

            namespace_members: set[str] = set()
            if snapshot_tree.has_export_list:
                for namespace, target_visible in namespace_targets:
                    if namespace not in exported_names:
                        continue
                    if namespace in local_all or namespace in imported:
                        continue
                    propagated = {
                        name: declarations
                        for name, declarations in target_visible.items()
                        if name not in local_all
                    }
                    imported = merge_maps((imported, propagated))
                    namespace_members.update(propagated)

            return apply_export_list(
                snapshot,
                imported,
                apply=apply_exports,
                namespace_members=frozenset(namespace_members),
            )

        return exported(
            importer,
            frozenset(),
            include_private_local=True,
        )

    def _nova_visible_function_declarations(
        self,
        importer: SemanticSnapshot,
        snapshots: tuple[SemanticSnapshot, ...],
        name: str,
    ) -> tuple[WorkspaceDeclaration, ...]:
        return self._nova_visible_function_map(importer, snapshots).get(name, ())

    def _nova_binding_names_for_declaration(
        self,
        importer: SemanticSnapshot,
        snapshots: tuple[SemanticSnapshot, ...],
        declaration: WorkspaceDeclaration,
    ) -> tuple[str, ...]:
        """Return unique importer-local names that resolve to one declaration."""
        visible = self._nova_visible_function_map(importer, snapshots)
        return tuple(
            sorted(
                name
                for name, candidates in visible.items()
                if len(candidates) == 1
                and candidates[0].snapshot is declaration.snapshot
                and candidates[0].symbol is declaration.symbol
            )
        )

    def _nova_import_alias_binding(
        self,
        importer: SemanticSnapshot,
        snapshots: tuple[SemanticSnapshot, ...],
        binding_name: str,
    ) -> tuple[NovaImportNameSyntax, WorkspaceDeclaration] | None:
        """Resolve one importer-local alias to its canonical declaration."""
        tree = importer.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return None
        indexed = {
            WorkspaceFolderSet.uri_identity(snapshot.uri): snapshot
            for snapshot in snapshots
        }
        matches: list[tuple[NovaImportNameSyntax, WorkspaceDeclaration]] = []
        for imported in tree.imports:
            if not imported.has_name_list:
                continue
            target_uri = self._nova_import_target_uri(importer.uri, imported.path)
            if target_uri is None:
                continue
            target = indexed.get(WorkspaceFolderSet.uri_identity(target_uri))
            if target is None:
                continue
            target_visible = self._nova_visible_function_map(
                target,
                snapshots,
                legacy_global=False,
                respect_root_exports=True,
            )
            for selected in imported.names:
                if (
                    selected.alias is None
                    or selected.alias_span is None
                    or selected.binding_name != binding_name
                ):
                    continue
                candidates = target_visible.get(selected.name, ())
                if len(candidates) != 1:
                    return None
                matches.append((selected, candidates[0]))
        if len(matches) != 1:
            return None

        selected, declaration = matches[0]
        visible = self._nova_visible_function_map(importer, snapshots)
        candidates = visible.get(binding_name, ())
        if (
            len(candidates) != 1
            or candidates[0].snapshot is not declaration.snapshot
            or candidates[0].symbol is not declaration.symbol
        ):
            return None
        return selected, declaration

    def _nova_import_namespace_bindings(
        self,
        importer: SemanticSnapshot,
        snapshots: tuple[SemanticSnapshot, ...],
    ) -> tuple[NovaNamespaceBinding, ...]:
        """Resolve unique star/selective importer-local namespace identities."""
        tree = importer.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return ()

        indexed = {
            WorkspaceFolderSet.uri_identity(snapshot.uri): snapshot
            for snapshot in snapshots
        }
        binding_counts: dict[str, int] = {}
        for imported in tree.imports:
            if imported.namespace is not None:
                binding_counts[imported.namespace] = (
                    binding_counts.get(imported.namespace, 0) + 1
                )
            if imported.has_name_list:
                for selected in imported.names:
                    binding_counts[selected.binding_name] = (
                        binding_counts.get(selected.binding_name, 0) + 1
                    )

        candidates: list[NovaNamespaceBinding] = []
        for imported in tree.imports:
            if (
                imported.namespace is not None
                and imported.namespace_span is not None
                and imported.namespace not in {"Int", "UInt"}
                and binding_counts.get(imported.namespace) == 1
            ):
                candidates.append(
                    NovaNamespaceBinding(
                        imported,
                        imported.namespace,
                        imported.namespace_span,
                    )
                )
                continue
            if not imported.has_name_list:
                continue
            target_uri = self._nova_import_target_uri(importer.uri, imported.path)
            if target_uri is None:
                continue
            target = indexed.get(WorkspaceFolderSet.uri_identity(target_uri))
            if target is None:
                continue
            namespace_exports = self._nova_exported_namespace_targets(
                target,
                snapshots,
            )
            if not namespace_exports:
                continue
            target_visible = self._nova_visible_function_map(
                target,
                snapshots,
                legacy_global=False,
                respect_root_exports=True,
            )
            for selected in imported.names:
                binding_name = selected.binding_name
                if (
                    binding_name in {"Int", "UInt"}
                    or binding_counts.get(binding_name) != 1
                    or selected.name not in namespace_exports
                    or selected.name in target_visible
                ):
                    continue
                candidates.append(
                    NovaNamespaceBinding(
                        imported,
                        binding_name,
                        selected.binding_span,
                        selected,
                    )
                )

        candidates.sort(key=lambda item: item.span.start)
        return tuple(candidates)

    def _nova_import_namespace_target(
        self,
        importer: SemanticSnapshot,
        snapshots: tuple[SemanticSnapshot, ...],
        offset: int,
    ) -> tuple[NovaNamespaceBinding, Span] | None:
        """Return one exact importer-local namespace binding addressed by syntax."""
        tree = importer.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return None

        bindings = self._nova_import_namespace_bindings(importer, snapshots)
        addressed_name: str | None = None
        addressed_span: Span | None = None
        for binding in bindings:
            if binding.span.start <= offset < binding.span.end:
                addressed_name = binding.name
                addressed_span = binding.span
                break

        if addressed_name is None:
            for namespace, span in tree.namespace_references:
                if span.start <= offset < span.end:
                    addressed_name = namespace
                    addressed_span = span
                    break

        if addressed_name is None or addressed_span is None:
            return None

        matches = tuple(
            binding for binding in bindings if binding.name == addressed_name
        )
        if len(matches) != 1:
            return None
        return matches[0], addressed_span

    @staticmethod
    def _nova_import_namespace_spans(
        importer: SemanticSnapshot,
        imported: NovaNamespaceBinding,
    ) -> tuple[Span, ...]:
        """Return declaration plus exact qualifier references for one local namespace."""
        tree = importer.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return ()
        spans = [imported.span]
        spans.extend(
            span
            for namespace, span in tree.namespace_references
            if namespace == imported.name
        )
        spans.sort(key=lambda span: span.start)
        return tuple(spans)


    def _nova_import_alias_target(
        self,
        importer: SemanticSnapshot,
        snapshots: tuple[SemanticSnapshot, ...],
        offset: int,
    ) -> tuple[NovaImportNameSyntax, WorkspaceDeclaration, Span] | None:
        """Return the exact alias binding addressed by syntax or one aliased call."""
        tree = importer.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return None

        for imported in tree.imports:
            if not imported.has_name_list:
                continue
            for selected in imported.names:
                alias_span = selected.alias_span
                if (
                    selected.alias is None
                    or alias_span is None
                    or not (alias_span.start <= offset < alias_span.end)
                ):
                    continue
                binding = self._nova_import_alias_binding(
                    importer,
                    snapshots,
                    selected.binding_name,
                )
                if binding is None or binding[0] is not selected:
                    return None
                return selected, binding[1], alias_span

        for call_name, span in tree.calls:
            if not (span.start <= offset < span.end):
                continue
            binding = self._nova_import_alias_binding(
                importer,
                snapshots,
                call_name,
            )
            if binding is None:
                return None
            return binding[0], binding[1], span
        return None

    @staticmethod
    def _nova_import_alias_is_outward(
        tree: NovaFunctionSyntax,
        binding_name: str,
    ) -> bool:
        """Whether one alias is part of the module's outward function view."""
        if not tree.has_export_list:
            return True
        return any(exported.name == binding_name for exported in tree.exports)

    @staticmethod
    def _nova_import_namespace_is_outward(
        tree: NovaFunctionSyntax,
        binding_name: str,
    ) -> bool:
        """Whether one namespace binding is explicitly exported by its module."""
        return tree.has_export_list and any(
            exported.name == binding_name for exported in tree.exports
        )

    @staticmethod
    def _nova_import_diagnostic_code(code: str | None) -> bool:
        """Whether one diagnostic is wholly recomputed from the import graph."""
        return code in _NOVA_IMPORT_DIAGNOSTIC_CODES

    @staticmethod
    def _nova_module_dependency_edges(
        tree: NovaFunctionSyntax,
    ) -> tuple[Any, ...]:
        """Return exact local-file module edges that affect outward semantics."""
        return (*tree.imports, *tree.wildcard_exports)

    def _nova_import_cycle_edges(
        self,
        snapshots: tuple[SemanticSnapshot, ...],
    ) -> dict[WorkspaceUriIdentity, frozenset[Span]]:
        """Return resolved import edges that participate in one exact SCC cycle."""
        indexed = {
            WorkspaceFolderSet.uri_identity(snapshot.uri): snapshot
            for snapshot in snapshots
        }
        adjacency: dict[
            WorkspaceUriIdentity,
            set[WorkspaceUriIdentity],
        ] = {identity: set() for identity in indexed}
        edges: dict[
            WorkspaceUriIdentity,
            list[tuple[Span, WorkspaceUriIdentity]],
        ] = {identity: [] for identity in indexed}

        for identity, snapshot in indexed.items():
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                continue
            for item in self._nova_module_dependency_edges(tree):
                target_uri = self._nova_import_target_uri(snapshot.uri, item.path)
                if target_uri is None:
                    continue
                target_identity = WorkspaceFolderSet.uri_identity(target_uri)
                if target_identity not in indexed:
                    continue
                adjacency[identity].add(target_identity)
                edges[identity].append((item.span, target_identity))

        index = 0
        indexes: dict[WorkspaceUriIdentity, int] = {}
        lowlinks: dict[WorkspaceUriIdentity, int] = {}
        stack: list[WorkspaceUriIdentity] = []
        on_stack: set[WorkspaceUriIdentity] = set()
        components: list[tuple[WorkspaceUriIdentity, ...]] = []

        def visit(node: WorkspaceUriIdentity) -> None:
            nonlocal index
            indexes[node] = index
            lowlinks[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)

            for target in sorted(adjacency[node]):
                if target not in indexes:
                    visit(target)
                    lowlinks[node] = min(lowlinks[node], lowlinks[target])
                elif target in on_stack:
                    lowlinks[node] = min(lowlinks[node], indexes[target])

            if lowlinks[node] != indexes[node]:
                return
            component: list[WorkspaceUriIdentity] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(tuple(sorted(component)))

        for node in sorted(adjacency):
            if node not in indexes:
                visit(node)

        component_by_node: dict[WorkspaceUriIdentity, int] = {}
        cyclic_components: set[int] = set()
        for component_index, component in enumerate(components):
            for node in component:
                component_by_node[node] = component_index
            if len(component) > 1 or component[0] in adjacency[component[0]]:
                cyclic_components.add(component_index)

        cycle_edges: dict[WorkspaceUriIdentity, frozenset[Span]] = {}
        for source, source_edges in edges.items():
            component_index = component_by_node[source]
            if component_index not in cyclic_components:
                continue
            spans = frozenset(
                span
                for span, target in source_edges
                if component_by_node[target] == component_index
            )
            if spans:
                cycle_edges[source] = spans
        return cycle_edges

    @staticmethod
    def _nova_import_cycle_related_information(
        target: SemanticSnapshot,
        cycle_edges: dict[WorkspaceUriIdentity, frozenset[Span]],
    ) -> tuple[DiagnosticRelatedInformation, ...]:
        """Point at one deterministic continuation edge for a cross-file cycle."""
        target_identity = WorkspaceFolderSet.uri_identity(target.uri)
        target_spans = cycle_edges.get(target_identity, frozenset())
        if not target_spans:
            return ()
        tree = target.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return ()
        for item in self._nova_module_dependency_edges(tree):
            if item.span not in target_spans:
                continue
            kind = "import" if item in tree.imports else "wildcard export"
            return (
                DiagnosticRelatedInformation(
                    target.uri,
                    item.span,
                    f"{kind} cycle continues through '{item.path}'",
                    semantic=target,
                ),
            )
        return ()

    def _nova_import_diagnostics(
        self,
        snapshot: SemanticSnapshot,
        snapshots: Any,
        *,
        cycle_edges: dict[WorkspaceUriIdentity, frozenset[Span]] | None = None,
    ) -> tuple[Diagnostic, ...]:
        """Validate exact-workspace Nova imports, selections, and explicit exports."""
        tree = snapshot.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return ()
        snapshot_tuple = tuple(snapshots)
        indexed = {
            WorkspaceFolderSet.uri_identity(candidate.uri): candidate
            for candidate in snapshot_tuple
        }
        if cycle_edges is None:
            cycle_edges = self._nova_import_cycle_edges(snapshot_tuple)
        snapshot_identity = WorkspaceFolderSet.uri_identity(snapshot.uri)
        diagnostics: list[Diagnostic] = []
        seen_namespaces: set[str] = set()
        for item in tree.imports:
            if item.namespace is not None and item.namespace_span is not None:
                if item.namespace in {"Int", "UInt"}:
                    diagnostics.append(
                        Diagnostic(
                            item.namespace_span,
                            f"reserved import namespace '{item.namespace}'",
                            code="nova.reserved-import-namespace",
                            source="nova",
                        )
                    )
                elif item.namespace in seen_namespaces:
                    diagnostics.append(
                        Diagnostic(
                            item.namespace_span,
                            f"duplicate import namespace '{item.namespace}'",
                            code="nova.duplicate-import-namespace",
                            source="nova",
                        )
                    )
                seen_namespaces.add(item.namespace)
            target_uri = self._nova_import_target_uri(snapshot.uri, item.path)
            target = (
                None
                if target_uri is None
                else indexed.get(WorkspaceFolderSet.uri_identity(target_uri))
            )
            if target is None:
                diagnostics.append(
                    Diagnostic(
                        item.span,
                        f"unresolved import '{item.path}'",
                        code="nova.unresolved-import",
                        source="nova",
                    )
                )
                continue
            if item.span in cycle_edges.get(snapshot_identity, frozenset()):
                diagnostics.append(
                    Diagnostic(
                        item.span,
                        f"import cycle includes '{item.path}'",
                        code="nova.import-cycle",
                        source="nova",
                        related_information=(
                            ()
                            if target is snapshot
                            else self._nova_import_cycle_related_information(
                                target,
                                cycle_edges,
                            )
                        ),
                    )
                )
            if item.namespace is not None:
                continue
            if not item.has_name_list:
                continue

            target_visible = self._nova_visible_function_map(
                target,
                snapshot_tuple,
                legacy_global=False,
                respect_root_exports=True,
            )
            target_tree = target.symbols.syntax.tree
            target_private = (
                frozenset(target_tree.private_declarations)
                if isinstance(target_tree, NovaFunctionSyntax)
                else frozenset()
            )
            target_visible = {
                name: tuple(
                    declaration
                    for declaration in declarations
                    if not (
                        declaration.snapshot is target
                        and declaration.symbol.span in target_private
                    )
                )
                for name, declarations in target_visible.items()
            }
            target_visible = {
                name: declarations
                for name, declarations in target_visible.items()
                if declarations
            }

            namespace_exports = self._nova_exported_namespace_targets(
                target,
                snapshot_tuple,
            )
            seen_imports: set[str] = set()
            for selected in item.names:
                binding_name = selected.binding_name
                if binding_name in seen_imports:
                    diagnostics.append(
                        Diagnostic(
                            selected.binding_span,
                            f"duplicate imported binding '{binding_name}'",
                            code="nova.duplicate-import-name",
                            source="nova",
                        )
                    )
                    continue
                seen_imports.add(binding_name)

                candidates = target_visible.get(selected.name, ())
                namespace_target = namespace_exports.get(selected.name)
                if namespace_target is not None:
                    if candidates:
                        namespace_export_span = next(
                            (
                                exported.span
                                for exported in target_tree.exports
                                if exported.name == selected.name
                            ),
                            selected.span,
                        ) if isinstance(target_tree, NovaFunctionSyntax) else selected.span
                        related = [
                            DiagnosticRelatedInformation(
                                target.uri,
                                namespace_export_span,
                                (
                                    "candidate exported namespace "
                                    f"'{selected.name}' is here"
                                ),
                                semantic=target,
                            )
                        ]
                        related.extend(
                            DiagnosticRelatedInformation(
                                candidate.uri,
                                candidate.symbol.span,
                                (
                                    "candidate imported function declaration "
                                    f"'{selected.name}' is here"
                                ),
                                semantic=candidate.snapshot,
                            )
                            for candidate in candidates
                        )
                        diagnostics.append(
                            Diagnostic(
                                selected.span,
                                f"ambiguous imported name '{selected.name}'",
                                code="nova.ambiguous-import-name",
                                source="nova",
                                related_information=tuple(related),
                            )
                        )
                        continue
                    if binding_name in {"Int", "UInt"}:
                        diagnostics.append(
                            Diagnostic(
                                selected.binding_span,
                                f"reserved import namespace '{binding_name}'",
                                code="nova.reserved-import-namespace",
                                source="nova",
                            )
                        )
                        continue
                    if binding_name in seen_namespaces:
                        diagnostics.append(
                            Diagnostic(
                                selected.binding_span,
                                f"duplicate import namespace '{binding_name}'",
                                code="nova.duplicate-import-namespace",
                                source="nova",
                            )
                        )
                        continue
                    seen_namespaces.add(binding_name)
                    continue

                if not candidates:
                    diagnostics.append(
                        Diagnostic(
                            selected.span,
                            f"unresolved imported function '{selected.name}'",
                            code="nova.unresolved-import-name",
                            source="nova",
                        )
                    )
                    continue
                if len(candidates) > 1:
                    diagnostics.append(
                        Diagnostic(
                            selected.span,
                            f"ambiguous imported function '{selected.name}'",
                            code="nova.ambiguous-import-name",
                            source="nova",
                            related_information=tuple(
                                DiagnosticRelatedInformation(
                                    candidate.uri,
                                    candidate.symbol.span,
                                    (
                                        "candidate imported function declaration "
                                        f"'{selected.name}' is here"
                                    ),
                                    semantic=candidate.snapshot,
                                )
                                for candidate in candidates
                            ),
                        )
                    )

        for item in tree.wildcard_exports:
            target_uri = self._nova_import_target_uri(snapshot.uri, item.path)
            target = (
                None
                if target_uri is None
                else indexed.get(WorkspaceFolderSet.uri_identity(target_uri))
            )
            if target is None:
                diagnostics.append(
                    Diagnostic(
                        item.span,
                        f"unresolved wildcard export target '{item.path}'",
                        code="nova.unresolved-export-target",
                        source="nova",
                    )
                )
                continue
            if item.span in cycle_edges.get(snapshot_identity, frozenset()):
                diagnostics.append(
                    Diagnostic(
                        item.span,
                        f"import cycle includes wildcard export '{item.path}'",
                        code="nova.import-cycle",
                        source="nova",
                        related_information=(
                            ()
                            if target is snapshot
                            else self._nova_import_cycle_related_information(
                                target,
                                cycle_edges,
                            )
                        ),
                    )
                )

        if not tree.has_export_list:
            return tuple(diagnostics)

        visible = self._nova_visible_function_map(
            snapshot,
            snapshot_tuple,
            legacy_global=False,
        )
        private_spans = frozenset(tree.private_declarations)
        namespace_bindings: dict[str, list[NovaNamespaceBinding]] = {}
        for binding in self._nova_import_namespace_bindings(
            snapshot,
            snapshot_tuple,
        ):
            namespace_bindings.setdefault(binding.name, []).append(binding)
        star_namespace_spans: dict[str, list[Span]] = {}
        for imported in tree.imports:
            if imported.namespace is None or imported.namespace_span is None:
                continue
            star_namespace_spans.setdefault(imported.namespace, []).append(
                imported.namespace_span
            )
        namespace_exports = self._nova_exported_namespace_targets(
            snapshot,
            snapshot_tuple,
        )

        seen: set[str] = set()
        for item in tree.exports:
            if item.name in seen:
                diagnostics.append(
                    Diagnostic(
                        item.span,
                        f"duplicate export '{item.name}'",
                        code="nova.duplicate-export",
                        source="nova",
                    )
                )
                continue
            seen.add(item.name)

            candidates = visible.get(item.name, ())
            namespaces = tuple(namespace_bindings.get(item.name, ()))
            namespace_target = namespace_exports.get(item.name)
            namespace_spans = list(star_namespace_spans.get(item.name, ()))
            namespace_spans.extend(
                binding.span
                for binding in namespaces
                if binding.span not in namespace_spans
            )

            if len(star_namespace_spans.get(item.name, ())) > 1 or (
                namespace_target is not None and candidates
            ):
                related: list[DiagnosticRelatedInformation] = [
                    DiagnosticRelatedInformation(
                        snapshot.uri,
                        span,
                        f"candidate namespace binding '{item.name}' is here",
                        semantic=snapshot,
                    )
                    for span in namespace_spans
                ]
                related.extend(
                    DiagnosticRelatedInformation(
                        candidate.uri,
                        candidate.symbol.span,
                        f"candidate function declaration '{item.name}' is here",
                        semantic=candidate.snapshot,
                    )
                    for candidate in candidates
                )
                diagnostics.append(
                    Diagnostic(
                        item.span,
                        f"ambiguous export '{item.name}'",
                        code="nova.ambiguous-export",
                        source="nova",
                        related_information=tuple(related),
                    )
                )
                continue

            if namespace_target is not None:
                continue

            if not candidates:
                diagnostics.append(
                    Diagnostic(
                        item.span,
                        f"unresolved export '{item.name}'",
                        code="nova.unresolved-export",
                        source="nova",
                    )
                )
                continue
            if len(candidates) > 1:
                diagnostics.append(
                    Diagnostic(
                        item.span,
                        f"ambiguous export '{item.name}'",
                        code="nova.ambiguous-export",
                        source="nova",
                        related_information=tuple(
                            DiagnosticRelatedInformation(
                                candidate.uri,
                                candidate.symbol.span,
                                (
                                    f"candidate function declaration "
                                    f"'{item.name}' is here"
                                ),
                                semantic=candidate.snapshot,
                            )
                            for candidate in candidates
                        ),
                    )
                )
                continue

            candidate = candidates[0]
            if (
                candidate.snapshot is snapshot
                and candidate.symbol.span in private_spans
            ):
                diagnostics.append(
                    Diagnostic(
                        item.span,
                        f"private function '{item.name}' cannot be exported",
                        code="nova.private-export",
                        source="nova",
                        related_information=(
                            DiagnosticRelatedInformation(
                                snapshot.uri,
                                candidate.symbol.span,
                                (
                                    f"private function declaration "
                                    f"'{item.name}' is here"
                                ),
                                semantic=snapshot,
                            ),
                        ),
                    )
                )
        return tuple(diagnostics)

    def _nova_workspace_root_import_path(
        self,
        importer_uri: str,
        target_uri: str,
    ) -> str | None:
        """Render one target relative to the importer's most-specific workspace root."""
        folder_uri = self.workspace_folders.scope_uri_for(importer_uri)
        if folder_uri is None or not WorkspaceFolderSet._contains(
            folder_uri,
            target_uri,
        ):
            return None
        try:
            folder = urllib.parse.urlsplit(folder_uri)
            importer = urllib.parse.urlsplit(importer_uri)
            target = urllib.parse.urlsplit(target_uri)
        except ValueError:
            return None
        if (
            folder.scheme.lower() != "file"
            or importer.scheme.lower() != "file"
            or target.scheme.lower() != "file"
            or folder.query
            or folder.fragment
            or importer.query
            or importer.fragment
            or target.query
            or target.fragment
        ):
            return None
        importer_identity = WorkspaceFolderSet.uri_identity(importer_uri)
        target_identity = WorkspaceFolderSet.uri_identity(target_uri)
        if importer_identity[:2] != target_identity[:2]:
            return None
        root_path = WorkspaceFolderSet._normalized_path(folder_uri).rstrip("/") or "/"
        target_path = WorkspaceFolderSet._normalized_path(target_uri)
        relative = posixpath.relpath(target_path, start=root_path)
        if relative == ".." or relative.startswith("../"):
            return None
        return f"@/{relative}"

    @staticmethod
    def _nova_relative_import_path(
        importer_uri: str,
        target_uri: str,
    ) -> str | None:
        """Return one relative Nova import path preserving URI path spelling."""
        try:
            importer = urllib.parse.urlsplit(importer_uri)
            target = urllib.parse.urlsplit(target_uri)
        except ValueError:
            return None
        importer_identity = WorkspaceFolderSet.uri_identity(importer_uri)
        target_identity = WorkspaceFolderSet.uri_identity(target_uri)
        if (
            importer_identity[:2] != target_identity[:2]
            or importer_identity[0] != "file"
            or importer.query
            or importer.fragment
            or target.query
            or target.fragment
        ):
            return None
        base = posixpath.dirname(importer.path) or "/"
        relative = posixpath.relpath(target.path, start=base)
        if relative.startswith("../"):
            return relative
        return f"./{relative}"

    def _nova_import_rename_changes(
        self,
        snapshots: Any,
        renames: tuple[tuple[str, str], ...],
    ) -> dict[str, list[dict[str, Any]]]:
        """Plan exact import-path edits for one file-rename batch."""
        renamed = {
            WorkspaceFolderSet.uri_identity(old_uri): new_uri
            for old_uri, new_uri in renames
        }
        planned: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for snapshot in snapshots:
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax) or not tree.imports:
                continue
            importer_identity = WorkspaceFolderSet.uri_identity(snapshot.uri)
            post_importer_uri = renamed.get(importer_identity, snapshot.uri)
            source = self._source_text(snapshot.symbols.syntax.document.text)

            for item in tree.imports:
                target_uri = self._nova_import_target_uri(snapshot.uri, item.path)
                if target_uri is None:
                    continue
                target_identity = WorkspaceFolderSet.uri_identity(target_uri)
                if importer_identity not in renamed and target_identity not in renamed:
                    continue
                post_target_uri = renamed.get(target_identity, target_uri)
                replacement = (
                    self._nova_workspace_root_import_path(
                        post_importer_uri,
                        post_target_uri,
                    )
                    if item.path.startswith("@/")
                    else self._nova_relative_import_path(
                        post_importer_uri,
                        post_target_uri,
                    )
                )
                if replacement is None:
                    raise DocumentError(
                        f"rename cannot preserve Nova import '{item.path}' "
                        f"from {snapshot.uri}"
                    )
                if replacement == item.path:
                    continue
                planned.setdefault(snapshot.uri, []).append(
                    (
                        item.span.start,
                        {
                            "range": self._range(source, item.span),
                            "newText": replacement,
                        },
                    )
                )

        return {
            uri: [edit for _, edit in sorted(items, key=lambda entry: entry[0])]
            for uri, items in sorted(planned.items())
        }

    def _closed_workspace_diagnostic_snapshots(
        self,
        snapshots: tuple[SemanticSnapshot, ...],
    ) -> tuple[DiagnosticSnapshot, ...]:
        """Recompute closed-file diagnostics against exact import visibility."""
        rendered: list[DiagnosticSnapshot] = []
        cycle_edges = self._nova_import_cycle_edges(snapshots)
        for snapshot in snapshots:
            identity = WorkspaceFolderSet.uri_identity(snapshot.uri)
            if self._closed_workspace_uris.get(identity) != snapshot.uri:
                continue
            base = self._closed_workspace_base_diagnostics.get(identity)
            if base is None or base.semantic is not snapshot:
                continue
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                continue

            visible = self._nova_visible_function_map(snapshot, snapshots)
            functions = {
                name: [
                    (declaration.snapshot, declaration.symbol)
                    for declaration in declarations
                ]
                for name, declarations in visible.items()
            }

            diagnostics = [
                diagnostic
                for diagnostic in base.diagnostics
                if diagnostic.code
                in {
                    "nova.duplicate-function",
                    "nova.duplicate-parameter",
                    "nova.duplicate-variable",
                }
            ]
            for name, span in tree.calls:
                candidates = functions.get(name, [])
                if not candidates:
                    diagnostics.append(
                        Diagnostic(
                            span,
                            f"unresolved function '{name}'",
                            code="nova.unresolved-function",
                            source="nova",
                        )
                    )
                elif len(candidates) > 1:
                    diagnostics.append(
                        Diagnostic(
                            span,
                            f"ambiguous function call '{name}'",
                            code="nova.ambiguous-function",
                            source="nova",
                            related_information=tuple(
                                DiagnosticRelatedInformation(
                                    candidate_snapshot.uri,
                                    symbol.span,
                                    (
                                        f"candidate function declaration "
                                        f"'{name}' is here"
                                    ),
                                    semantic=candidate_snapshot,
                                )
                                for candidate_snapshot, symbol in candidates
                            ),
                        )
                    )

            diagnostics.extend(
                self._nova_import_diagnostics(
                    snapshot,
                    snapshots,
                    cycle_edges=cycle_edges,
                )
            )
            diagnostics.extend(
                self._closed_workspace_product_diagnostics(
                    snapshot,
                    functions,
                )
            )

            rendered.append(
                DiagnosticSnapshot(
                    semantic=snapshot,
                    diagnostics=tuple(
                        sorted(
                            diagnostics,
                            key=lambda diagnostic: (
                                diagnostic.span.start,
                                diagnostic.span.end,
                                diagnostic.severity,
                                diagnostic.message,
                                diagnostic.code or "",
                                diagnostic.source or "",
                            ),
                        )
                    ),
                )
            )
        return tuple(rendered)

    def _closed_workspace_product_diagnostics(
        self,
        snapshot: SemanticSnapshot,
        functions: dict[str, list[tuple[SemanticSnapshot, Any]]],
    ) -> tuple[Diagnostic, ...]:
        """Extension point for detached diagnostics owned by later product layers."""
        return ()

    def _closed_workspace_snapshots_current(
        self, snapshots: tuple[SemanticSnapshot, ...]
    ) -> bool:
        """Revalidate detached filesystem inputs before publishing source mutations."""
        for snapshot in snapshots:
            document = self.documents.get(snapshot.uri)
            if document is snapshot.symbols.syntax.document:
                continue
            identity = WorkspaceFolderSet.uri_identity(snapshot.uri)
            if self._closed_workspace_uris.get(identity) != snapshot.uri:
                return False
            item = read_closed_workspace_file(snapshot.uri)
            if item is None or item.text != snapshot.symbols.syntax.document.text:
                return False
        return True

    def _workspace_edit_versions(
        self, snapshots: tuple[SemanticSnapshot, ...]
    ) -> dict[str, int | None]:
        """Use exact buffer versions for open files and null for closed files."""
        versions: dict[str, int | None] = {}
        for snapshot in snapshots:
            document = self.documents.get(snapshot.uri)
            versions[snapshot.uri] = (
                document.version
                if document is snapshot.symbols.syntax.document
                else None
            )
        return versions

    def _workspace_documents(
        self, scope: WorkspaceFolderSnapshot | None = None
    ) -> tuple[Document, ...]:
        captured = self.workspace_folders.snapshot() if scope is None else scope
        return tuple(
            document
            for document in self.documents.snapshots()
            if captured.contains(document.uri)
        )

    def _workspace_folder_scope_changed(self, before: Any, after: Any) -> None:
        """React to capabilities keyed directly by folder membership."""
        self._queue_closed_workspace_watch_registration()

    def _workspace_scope_changed(self, before: Any, after: Any) -> None:
        """Extension point for capabilities that cache workspace-wide results."""

    def _publish_workspace_diagnostics(self) -> None:
        """Reconcile Nova call diagnostics against one exact workspace snapshot set."""
        snapshots = self.workspace_symbols.snapshots()
        cycle_edges = self._nova_import_cycle_edges(tuple(snapshots))
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
                }
                and diagnostic.code not in _NOVA_IMPORT_DIAGNOSTIC_CODES
            ]
            visible = self._nova_visible_function_map(snapshot, snapshots)
            for name, span in tree.calls:
                declarations = visible.get(name, ())
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
            diagnostics.extend(
                self._nova_import_diagnostics(
                    snapshot,
                    snapshots,
                    cycle_edges=cycle_edges,
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

    def _workspace_function_query(self, params: Any):
        parsed = self._semantic_query(params)
        if parsed is None:
            return None
        semantics, offset, _ = parsed
        if semantics is None:
            return None
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return None

        for call_name, span in tree.calls:
            if span.start <= offset < span.end:
                return semantics, call_name
        target = semantics.definition_at(offset)
        if target is not None:
            if target.kind != "function":
                return None
            return semantics, target.name
        return None

    def _handle_nova_document_links(
        self,
        request_id: Any,
        params: Any,
    ) -> dict[str, Any]:
        """Return exact target links for resolved top-level Nova imports."""
        uri = self._document_uri(params)
        if uri is None:
            return self._error(request_id, -32602, "Invalid params")

        document = self.documents.get(uri)
        semantics = self.semantics.get(uri)
        if (
            document is None
            or document.language_id != self.nova_adapter.language_id
            or semantics is None
            or semantics.symbols.syntax.document is not document
            or not isinstance(semantics.symbols.syntax.tree, NovaFunctionSyntax)
        ):
            return self._result(request_id, [])

        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            source = self._source_text(document.text)
            indexed = {
                WorkspaceFolderSet.uri_identity(snapshot.uri): snapshot.uri
                for snapshot in snapshots
            }
            links: list[dict[str, Any]] = []
            for item in semantics.symbols.syntax.tree.imports:
                target_uri = self._nova_import_target_uri(semantics.uri, item.path)
                if target_uri is None:
                    continue
                indexed_uri = indexed.get(
                    WorkspaceFolderSet.uri_identity(target_uri)
                )
                if indexed_uri is None:
                    continue
                links.append(
                    {
                        "range": self._range(source, item.span),
                        "target": indexed_uri,
                    }
                )
            self.requests.checkpoint(context)

            def publish() -> dict[str, Any]:
                self.requests.checkpoint(context)
                return self._result(request_id, links)

            try:
                return self.semantics.commit_if_current(
                    semantics,
                    lambda: self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        publish,
                    ),
                )
            except (SemanticError, WorkspaceIndexError):
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _handle_project_function_moniker(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        query = self._workspace_function_query(params)
        if query is None:
            return self._result(request_id, [])
        semantics, name = query
        snapshots = self.workspace_symbols.snapshots()
        folder_scope = self.workspace_folders.snapshot()

        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            declarations = self._nova_visible_function_declarations(
                semantics,
                snapshots,
                name,
            )
            result = (
                []
                if len(declarations) != 1
                else self._project_function_moniker(
                    declarations[0],
                    snapshots=snapshots,
                    folder_scope=folder_scope,
                )
            )
            self.requests.checkpoint(context)

            def publish() -> dict[str, Any]:
                self.requests.checkpoint(context)
                return self._result(request_id, result)

            try:
                return self.semantics.commit_if_current(
                    semantics,
                    lambda: self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        lambda: self.workspace_folders.commit_if_current(
                            folder_scope.generation,
                            publish,
                        ),
                    ),
                )
            except (SemanticError, WorkspaceIndexError, WorkspaceFolderError):
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _project_function_moniker(
        self,
        declaration: WorkspaceDeclaration,
        *,
        snapshots: Any,
        folder_scope: WorkspaceFolderSnapshot,
    ) -> list[dict[str, str]]:
        """Return one conservative project-level moniker for an exact function."""
        if not any(snapshot is declaration.snapshot for snapshot in snapshots):
            return []
        project_uri = folder_scope.scope_uri_for(declaration.uri)
        if project_uri is None:
            return []

        return [
            {
                "scheme": "nova",
                "identifier": declaration.symbol.name,
                "unique": "project",
                "kind": "local",
            }
        ]

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return None
        semantics, _, _ = parsed
        if semantics is None or not isinstance(
            semantics.symbols.syntax.tree, NovaFunctionSyntax
        ):
            return None

        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            items: set[tuple[str, str]] = {
                (symbol.name, symbol.kind) for symbol in semantics.symbols.symbols
            }
            visible = self._nova_visible_function_map(semantics, snapshots)
            for binding_name, declarations in visible.items():
                for declaration in declarations:
                    items.add((binding_name, declaration.symbol.kind))

            result = [
                {"label": name, "detail": kind}
                for name, kind in sorted(items, key=lambda item: (item[0], item[1]))
            ]
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

    @staticmethod
    def _function_signature(declaration: Any) -> str:
        """Return the exact bounded Nova function header owning a declaration."""
        text = declaration.snapshot.symbols.syntax.document.text
        span = declaration.symbol.span
        start = text.rfind("fn", 0, span.start)
        if start < 0 or text[start + 2 : span.start].strip():
            return f"function {declaration.symbol.name}"
        opening = text.find("{", span.end)
        if opening < 0:
            return f"function {declaration.symbol.name}"
        signature = text[start:opening].strip()
        return signature or f"function {declaration.symbol.name}"

    def _function_extent(self, declaration: Any) -> Span:
        text = declaration.snapshot.symbols.syntax.document.text
        selection = declaration.symbol.span
        start = text.rfind("fn", 0, selection.start)
        opening = text.find("{", selection.end)
        if start < 0 or opening < 0:
            return selection
        closing = self.nova_adapter._matching_brace(text, opening)
        if closing is None:
            return selection
        return Span(start, closing + 1)

    def _call_hierarchy_item(self, declaration: Any) -> dict[str, Any]:
        source = self._source_text(declaration.snapshot.symbols.syntax.document.text)
        return {
            "name": declaration.symbol.name,
            "kind": 12,
            "detail": self._function_signature(declaration),
            "uri": declaration.uri,
            "range": self._range(source, self._function_extent(declaration)),
            "selectionRange": self._range(source, declaration.symbol.span),
            "data": {"name": declaration.symbol.name, "uri": declaration.uri},
        }

    def _call_hierarchy_declaration(self, params: Any):
        if not isinstance(params, dict):
            return None
        item = params.get("item")
        if not isinstance(item, dict):
            return None
        data = item.get("data")
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        uri = data.get("uri")
        if not isinstance(name, str) or not isinstance(uri, str):
            return None
        declarations = tuple(
            declaration
            for declaration in self.workspace_symbols.declarations(name)
            if declaration.symbol.kind == "function" and declaration.uri == uri
        )
        return declarations[0] if len(declarations) == 1 else None

    def _owning_function_declaration(self, snapshot: Any, offset: int):
        candidates = []
        for symbol in snapshot.symbols.symbols:
            if symbol.kind != "function":
                continue
            declaration = next(
                (
                    candidate
                    for candidate in self.workspace_symbols.declarations(symbol.name)
                    if candidate.snapshot is snapshot and candidate.symbol is symbol
                ),
                None,
            )
            if declaration is None:
                continue
            extent = self._function_extent(declaration)
            if extent.start <= offset < extent.end:
                candidates.append(declaration)
        if not candidates:
            return None
        return min(candidates, key=lambda declaration: self._function_extent(declaration).end)

    def _handle_prepare_call_hierarchy(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        query = self._workspace_function_query(params)
        if query is None:
            return self._result(request_id, [])
        semantics, name = query
        snapshots = self.workspace_symbols.snapshots()
        declarations = self._nova_visible_function_declarations(
            semantics,
            snapshots,
            name,
        )
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")
        try:
            self.requests.checkpoint(context)
            result = [self._call_hierarchy_item(declarations[0])] if len(declarations) == 1 else []
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

    def _handle_call_hierarchy_calls(
        self, method: str, request_id: Any, params: Any
    ) -> dict[str, Any]:
        declaration = self._call_hierarchy_declaration(params)
        if declaration is None:
            return self._result(request_id, [])
        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=declaration.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")
        try:
            self.requests.checkpoint(context)
            if method == "callHierarchy/incomingCalls":
                result = self._incoming_calls(declaration, snapshots)
            else:
                result = self._outgoing_calls(declaration, snapshots)
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

    def _incoming_calls(self, declaration: Any, snapshots: tuple[Any, ...]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, int], tuple[Any, list[Span]]] = {}
        for snapshot in snapshots:
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                continue
            bindings = self._nova_binding_names_for_declaration(
                snapshot,
                snapshots,
                declaration,
            )
            if not bindings:
                continue
            for call_name, span in tree.calls:
                if call_name not in bindings:
                    continue
                caller = self._owning_function_declaration(snapshot, span.start)
                if caller is None:
                    continue
                key = (caller.uri, caller.symbol.span.start)
                grouped.setdefault(key, (caller, []))[1].append(span)
        result = []
        for key in sorted(grouped):
            caller, spans = grouped[key]
            source = self._source_text(caller.snapshot.symbols.syntax.document.text)
            result.append(
                {
                    "from": self._call_hierarchy_item(caller),
                    "fromRanges": [
                        self._range(source, span)
                        for span in sorted(spans, key=lambda item: item.start)
                    ],
                }
            )
        return result

    def _outgoing_calls(
        self,
        declaration: Any,
        snapshots: tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        snapshot = declaration.snapshot
        tree = snapshot.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return []
        extent = self._function_extent(declaration)
        source = self._source_text(snapshot.symbols.syntax.document.text)
        grouped: dict[tuple[str, int], tuple[Any, list[Span]]] = {}
        for call_name, span in tree.calls:
            if not (extent.start <= span.start < extent.end):
                continue
            targets = self._nova_visible_function_declarations(
                snapshot,
                snapshots,
                call_name,
            )
            if len(targets) != 1:
                continue
            target = targets[0]
            key = (target.uri, target.symbol.span.start)
            grouped.setdefault(key, (target, []))[1].append(span)
        result = []
        for key in sorted(grouped):
            target, spans = grouped[key]
            result.append(
                {
                    "to": self._call_hierarchy_item(target),
                    "fromRanges": [
                        self._range(source, span)
                        for span in sorted(spans, key=lambda item: item.start)
                    ],
                }
            )
        return result

    def _handle_workspace_hover(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return None
        semantics, offset, source = parsed
        if semantics is None:
            return None
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return None

        target = semantics.definition_at(offset)
        if target is not None and target.kind != "function":
            return None

        call = next(
            (
                (call_name, span)
                for call_name, span in tree.calls
                if span.start <= offset < span.end
            ),
            None,
        )
        if target is not None:
            name = target.name
            hover_span = call[1] if call is not None else target.span
        elif call is not None:
            name, hover_span = call
        else:
            return None

        snapshots = self.workspace_symbols.snapshots()
        declarations = self._nova_visible_function_declarations(
            semantics,
            snapshots,
            name,
        )
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result: Any = None
            if len(declarations) == 1:
                result = {
                    "contents": {
                        "kind": "plaintext",
                        "value": self._function_signature(declarations[0]),
                    },
                    "range": self._range(source, hover_span),
                }
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

    def _handle_workspace_navigation(
        self, method: str, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        """Resolve importer-local namespaces or Nova functions from exact snapshots."""
        parsed = self._semantic_query(params)
        if parsed is not None:
            semantics, offset, source = parsed
            if semantics is not None:
                snapshots = self.workspace_symbols.snapshots()
                namespace_target = self._nova_import_namespace_target(
                    semantics,
                    snapshots,
                    offset,
                )
                if namespace_target is not None:
                    imported, _ = namespace_target
                    spans = self._nova_import_namespace_spans(
                        semantics,
                        imported,
                    )
                    try:
                        context = self.requests.start(request_id, uri=semantics.uri)
                    except RequestError:
                        return self._error(request_id, -32602, "Invalid params")
                    try:
                        self.requests.checkpoint(context)
                        if method == "textDocument/definition":
                            declaration = imported.span
                            result: Any = (
                                None
                                if declaration is None
                                else self._location(
                                    semantics.uri,
                                    source,
                                    declaration,
                                )
                            )
                        else:
                            include_declaration = self._include_declaration(params)
                            if include_declaration is None:
                                return self._error(
                                    request_id,
                                    -32602,
                                    "Invalid params",
                                )
                            namespace_spans = (
                                spans
                                if include_declaration
                                else tuple(
                                    span
                                    for span in spans
                                    if span != imported.span
                                )
                            )
                            result = [
                                self._location(
                                    semantics.uri,
                                    source,
                                    span,
                                )
                                for span in namespace_spans
                            ]
                        self.requests.checkpoint(context)
                        try:
                            return self.workspace_symbols.commit_snapshots_if_current(
                                snapshots,
                                lambda: self._result(request_id, result),
                            )
                        except WorkspaceIndexError:
                            return self._error(
                                request_id,
                                -32801,
                                "Content modified",
                            )
                    except RequestCancelled:
                        return self._error(
                            request_id,
                            -32800,
                            "Request cancelled",
                        )
                    except StaleRequest:
                        return self._error(
                            request_id,
                            -32801,
                            "Content modified",
                        )
                    finally:
                        self.requests.finish(context)

        query = self._workspace_function_query(params)
        if query is None:
            return None
        semantics, name = query

        snapshots = self.workspace_symbols.snapshots()
        declarations = self._nova_visible_function_declarations(
            semantics,
            snapshots,
            name,
        )
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            if len(declarations) != 1:
                result = [] if method == "textDocument/references" else None
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, lambda: self._result(request_id, result)
                )

            declaration = declarations[0]
            if method == "textDocument/definition":
                source = self._source_text(declaration.snapshot.symbols.syntax.document.text)
                result = self._location(
                    declaration.uri, source, declaration.symbol.span
                )
            else:
                include_declaration = self._include_declaration(params)
                if include_declaration is None:
                    return self._error(request_id, -32602, "Invalid params")
                locations: list[tuple[str, int, dict[str, Any]]] = []
                if include_declaration:
                    source = self._source_text(declaration.snapshot.symbols.syntax.document.text)
                    locations.append(
                        (
                            declaration.uri,
                            declaration.symbol.span.start,
                            self._location(
                                declaration.uri, source, declaration.symbol.span
                            ),
                        )
                    )
                for snapshot in snapshots:
                    snapshot_tree = snapshot.symbols.syntax.tree
                    if not isinstance(snapshot_tree, NovaFunctionSyntax):
                        continue
                    bindings = self._nova_binding_names_for_declaration(
                        snapshot,
                        snapshots,
                        declaration,
                    )
                    if not bindings:
                        continue
                    source = self._source_text(snapshot.symbols.syntax.document.text)
                    for call_name, span in snapshot_tree.calls:
                        if call_name in bindings:
                            locations.append(
                                (
                                    snapshot.uri,
                                    span.start,
                                    self._location(snapshot.uri, source, span),
                                )
                            )
                locations.sort(key=lambda item: (item[0], item[1]))
                result = [location for _, _, location in locations]

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

    def _handle_workspace_prepare_rename(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return None
        semantics, offset, source = parsed
        if semantics is None:
            return None
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return None

        if semantics.definition_at(offset) is not None:
            return None

        snapshots = self.workspace_symbols.snapshots()
        alias_target = self._nova_import_alias_target(
            semantics,
            snapshots,
            offset,
        )
        call = next(
            (
                (call_name, span)
                for call_name, span in tree.calls
                if span.start <= offset < span.end
            ),
            None,
        )
        if alias_target is None and call is None:
            return None

        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result: Any = None
            if alias_target is not None:
                selected, _, target_span = alias_target
                binding_name = selected.binding_name
                if not self._nova_import_alias_is_outward(tree, binding_name):
                    result = {
                        "range": self._range(source, target_span),
                        "placeholder": binding_name,
                    }
            elif call is not None:
                name, call_span = call
                declarations = self._nova_visible_function_declarations(
                    semantics,
                    snapshots,
                    name,
                )
                if (
                    len(declarations) == 1
                    and declarations[0].symbol.name == name
                ):
                    result = {
                        "range": self._range(source, call_span),
                        "placeholder": name,
                    }
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

    def _handle_workspace_rename(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        new_name = params.get("newName")
        if not isinstance(new_name, str) or not new_name:
            return self._error(request_id, -32602, "Invalid params")

        query = self._workspace_function_query(params)
        if query is None:
            return None
        semantics, name = query
        snapshots = self.workspace_symbols.snapshots()
        declarations = self._nova_visible_function_declarations(
            semantics,
            snapshots,
            name,
        )
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            if len(declarations) != 1:
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots, lambda: self._result(request_id, None)
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            declaration = declarations[0]
            edits_by_uri: dict[str, list[tuple[int, dict[str, Any]]]] = {}
            declaration_source = self._source_text(
                declaration.snapshot.symbols.syntax.document.text
            )
            edits_by_uri.setdefault(declaration.uri, []).append(
                (
                    declaration.symbol.span.start,
                    {
                        "range": self._range(
                            declaration_source, declaration.symbol.span
                        ),
                        "newText": new_name,
                    },
                )
            )

            for snapshot in snapshots:
                snapshot_tree = snapshot.symbols.syntax.tree
                if not isinstance(snapshot_tree, NovaFunctionSyntax):
                    continue
                resolved = self._nova_visible_function_declarations(
                    snapshot,
                    snapshots,
                    name,
                )
                if (
                    len(resolved) != 1
                    or resolved[0].snapshot is not declaration.snapshot
                    or resolved[0].symbol is not declaration.symbol
                ):
                    continue
                source = self._source_text(snapshot.symbols.syntax.document.text)
                for call_name, span in snapshot_tree.calls:
                    if call_name != name:
                        continue
                    edits_by_uri.setdefault(snapshot.uri, []).append(
                        (
                            span.start,
                            {"range": self._range(source, span), "newText": new_name},
                        )
                    )

            changes: dict[str, list[dict[str, Any]]] = {}
            for uri in sorted(edits_by_uri):
                ordered = sorted(edits_by_uri[uri], key=lambda item: item[0])
                changes[uri] = [edit for _, edit in ordered]

            versions = self._workspace_edit_versions(snapshots)
            workspace_edit = self._workspace_edit(
                changes,
                versions=versions,
                annotation_label=f"Rename '{name}' to '{new_name}'",
            )
            self.requests.checkpoint(context)
            if not self._closed_workspace_snapshots_current(snapshots):
                self._refresh_closed_workspace_files()
                return self._error(request_id, -32801, "Content modified")
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots,
                    lambda: self._result(request_id, workspace_edit),
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _include_declaration(params: Any) -> bool | None:
        if not isinstance(params, dict):
            return None
        context = params.get("context")
        if context is None:
            return False
        if not isinstance(context, dict):
            return None
        include = context.get("includeDeclaration")
        return include if isinstance(include, bool) else None

    def _handle_workspace_symbol(self, request_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict) or not isinstance(params.get("query"), str):
            return self._error(request_id, -32602, "Invalid params")
        valid_partial, partial_result_token = self._workspace_symbol_progress_token(
            params, "partialResultToken"
        )
        if not valid_partial:
            return self._error(request_id, -32602, "Invalid params")
        valid_work_done, work_done_token = self._workspace_symbol_progress_token(
            params, "workDoneToken"
        )
        if not valid_work_done:
            return self._error(request_id, -32602, "Invalid params")
        active_work_done_token = (
            work_done_token if self._work_done_progress_support else None
        )

        try:
            context = self.requests.start(request_id)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        snapshots = self.workspace_symbols.snapshots()
        work_done_started = False
        try:
            self.requests.checkpoint(context)
            if active_work_done_token is not None:
                self._queue_progress(
                    active_work_done_token,
                    {
                        "kind": "begin",
                        "title": "Workspace symbols",
                        "cancellable": True,
                        "percentage": 0,
                    },
                )
                work_done_started = True

            declarations = self.workspace_symbols.search(params["query"])
            self.requests.checkpoint(context)
            result = []
            total_symbols = len(declarations)
            for index, declaration in enumerate(declarations, start=1):
                self.requests.checkpoint(context)
                source = self._source_text(
                    declaration.snapshot.symbols.syntax.document.text
                )
                result.append(
                    {
                        "name": declaration.symbol.name,
                        "kind": _SYMBOL_KINDS.get(declaration.symbol.kind, 13),
                        "location": {
                            "uri": declaration.uri,
                            "range": self._range(source, declaration.symbol.span),
                        },
                    }
                )
                if (
                    active_work_done_token is not None
                    and (
                        index % _WORKSPACE_SYMBOL_PARTIAL_CHUNK_SIZE == 0
                        or index == total_symbols
                    )
                ):
                    self._queue_progress(
                        active_work_done_token,
                        {
                            "kind": "report",
                            "message": (
                                f"Processed {index} of {total_symbols} workspace symbols"
                            ),
                            "percentage": (
                                100
                                if total_symbols == 0
                                else (index * 100) // total_symbols
                            ),
                        },
                    )

            self.requests.checkpoint(context)

            def publish() -> dict[str, Any]:
                if partial_result_token is None:
                    return self._result(request_id, result)
                for chunk_start in range(
                    0, len(result), _WORKSPACE_SYMBOL_PARTIAL_CHUNK_SIZE
                ):
                    self._queue_progress(
                        partial_result_token,
                        result[
                            chunk_start : chunk_start
                            + _WORKSPACE_SYMBOL_PARTIAL_CHUNK_SIZE
                        ],
                    )
                return self._result(request_id, [])

            try:
                response = self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, publish
                )
            except WorkspaceIndexError as exc:
                raise StaleRequest("workspace symbol inputs changed") from exc

            if active_work_done_token is not None:
                self._queue_progress(
                    active_work_done_token,
                    {"kind": "end", "message": "Workspace symbol search complete"},
                )
            return response
        except RequestCancelled:
            if work_done_started and active_work_done_token is not None:
                self._queue_progress(
                    active_work_done_token,
                    {"kind": "end", "message": "Workspace symbol search cancelled"},
                )
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            if work_done_started and active_work_done_token is not None:
                self._queue_progress(
                    active_work_done_token,
                    {"kind": "end", "message": "Workspace symbol search changed"},
                )
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _workspace_symbol_progress_token(
        params: dict[str, Any], key: str
    ) -> tuple[bool, str | int | None]:
        if key not in params:
            return True, None
        token = params[key]
        if isinstance(token, bool) or not isinstance(token, str | int):
            return False, None
        return True, token

    @staticmethod
    def _client_supports_document_links(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("documentLink"), dict)

    @staticmethod
    def _client_supports_moniker(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("moniker"), dict)

    @staticmethod
    def _client_supports_file_will_create(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        file_operations = workspace.get("fileOperations")
        return (
            isinstance(file_operations, dict)
            and file_operations.get("willCreate") is True
        )

    @staticmethod
    def _client_supports_file_will_delete(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        file_operations = workspace.get("fileOperations")
        return (
            isinstance(file_operations, dict)
            and file_operations.get("willDelete") is True
        )

    @staticmethod
    def _client_supports_file_create(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        file_operations = workspace.get("fileOperations")
        return (
            isinstance(file_operations, dict)
            and file_operations.get("didCreate") is True
        )

    @staticmethod
    def _client_supports_file_delete(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        file_operations = workspace.get("fileOperations")
        return (
            isinstance(file_operations, dict)
            and file_operations.get("didDelete") is True
        )

    @staticmethod
    def _client_supports_file_will_rename(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        file_operations = workspace.get("fileOperations")
        return (
            isinstance(file_operations, dict)
            and file_operations.get("willRename") is True
        )

    @staticmethod
    def _client_supports_file_rename(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        file_operations = workspace.get("fileOperations")
        return (
            isinstance(file_operations, dict)
            and file_operations.get("didRename") is True
        )

    @staticmethod
    def _client_supports_workspace_folders(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        return (
            isinstance(workspace, dict)
            and workspace.get("workspaceFolders") is True
        )

    @staticmethod
    def _client_supports_workspace_symbol(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        return isinstance(workspace.get("symbol"), dict)

    @staticmethod
    def _client_supports_call_hierarchy(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("callHierarchy"), dict)
