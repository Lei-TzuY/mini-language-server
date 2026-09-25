"""Initialization-authoritative Nova bare-module search roots."""

from __future__ import annotations

import urllib.parse
from typing import Any

from .server import ServerState
from .will_save_formatting import (
    _FORMATTING_CONFIGURATION_REGISTRATION_ID,
    _FORMATTING_CONFIGURATION_SECTION,
)
from .will_save_formatting import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace_files import local_path_from_file_uri
from .workspace_folders import WorkspaceFolderSet
from .workspace_lsp import NovaModuleResolution

_MODULE_SEARCH_CONFIGURATION_SECTION = "mini-language-server.nova.moduleSearchRoots"
_MODULE_SEARCH_CONFIGURATION_REGISTRATION_ID = (
    "mini-language-server.nova.moduleSearchRoots.didChangeConfiguration"
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Add bounded precedence-ordered bare-module roots from initialization."""

    def __init__(self) -> None:
        super().__init__()
        self._module_search_configuration_enabled = False
        self._initial_module_search_roots: tuple[str, ...] | None = None
        self._module_search_roots: tuple[str, ...] | None = None
        self._module_search_configuration_generation = 0
        self._module_search_configuration_requests: dict[str, int] = {}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            params = message.get("params")
            self._module_search_configuration_enabled = (
                self._has_module_search_roots_option(params)
            )
            roots = self._parse_module_search_roots(params)
            self._initial_module_search_roots = roots
            self._module_search_roots = roots
        elif (
            method == "workspace/didChangeConfiguration"
            and "id" not in message
            and self.state is ServerState.RUNNING
            and self._workspace_configuration_support
            and self._module_search_configuration_enabled
        ):
            with self._formatting_configuration_lock:
                self._module_search_configuration_generation += 1
        return super().handle(message)

    @staticmethod
    def _has_module_search_roots_option(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        options = params.get("initializationOptions")
        if not isinstance(options, dict):
            return False
        nova = options.get("nova")
        return isinstance(nova, dict) and "moduleSearchRoots" in nova

    @staticmethod
    def _parse_module_search_roots(
        params: Any,
    ) -> tuple[str, ...] | None:
        """Return explicit canonical root order, None when no policy is configured."""
        if not isinstance(params, dict):
            return None
        options = params.get("initializationOptions")
        if not isinstance(options, dict):
            return None
        nova = options.get("nova")
        if not isinstance(nova, dict) or "moduleSearchRoots" not in nova:
            return None
        return NovaProductLanguageServer._parse_module_search_root_values(
            nova.get("moduleSearchRoots")
        )

    @staticmethod
    def _parse_module_search_root_values(
        configured: Any,
    ) -> tuple[str, ...]:
        """Parse one explicit root list; malformed values fail closed."""
        if not isinstance(configured, list):
            return ()

        roots: list[str] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for value in configured:
            if not isinstance(value, str) or not value:
                return ()
            try:
                parsed = urllib.parse.urlsplit(value)
                identity = WorkspaceFolderSet.uri_identity(value)
            except ValueError:
                return ()
            if (
                parsed.scheme.lower() != "file"
                or parsed.query
                or parsed.fragment
                or local_path_from_file_uri(value) is None
            ):
                return ()
            if identity in seen:
                continue
            seen.add(identity)
            roots.append(urllib.parse.urlunsplit(identity))
        return tuple(roots)

    def _queue_formatting_configuration_registration(self) -> None:
        """Register both configuration sections only after explicit module opt-in."""
        if not self._module_search_configuration_enabled:
            super()._queue_formatting_configuration_registration()
            return
        if (
            not self._workspace_configuration_support
            or not self._did_change_configuration_dynamic_registration
            or self._formatting_configuration_registration_attempted
        ):
            return
        self._formatting_configuration_registration_attempted = True

        def own_registration(request_id: str) -> None:
            self._formatting_configuration_registration_request = request_id

        self._queue_server_request(
            "client/registerCapability",
            {
                "registrations": [
                    {
                        "id": _FORMATTING_CONFIGURATION_REGISTRATION_ID,
                        "method": "workspace/didChangeConfiguration",
                        "registerOptions": {
                            "section": _FORMATTING_CONFIGURATION_SECTION,
                        },
                    },
                    {
                        "id": _MODULE_SEARCH_CONFIGURATION_REGISTRATION_ID,
                        "method": "workspace/didChangeConfiguration",
                        "registerOptions": {
                            "section": _MODULE_SEARCH_CONFIGURATION_SECTION,
                        },
                    },
                ]
            },
            on_queued=own_registration,
        )

    def _queue_formatting_configuration(self) -> None:
        """Queue one exact request for formatting plus opted-in module authority."""
        if not self._module_search_configuration_enabled:
            super()._queue_formatting_configuration()
            return
        if (
            not self._workspace_configuration_support
            or self._has_pending_server_request("workspace/configuration")
        ):
            return
        scopes = self._formatting_configuration_scopes()
        items: list[dict[str, Any]] = []
        for scope in scopes:
            item: dict[str, Any] = {
                "section": _FORMATTING_CONFIGURATION_SECTION
            }
            if scope is not None:
                item["scopeUri"] = scope
            items.append(item)
        items.append({"section": _MODULE_SEARCH_CONFIGURATION_SECTION})
        with self._formatting_configuration_lock:
            formatting_generation = self._formatting_configuration_generation
            module_generation = self._module_search_configuration_generation

        def own_configuration(request_id: str) -> None:
            with self._formatting_configuration_lock:
                self._formatting_configuration_requests[request_id] = (
                    formatting_generation,
                    scopes,
                )
                self._module_search_configuration_requests[request_id] = (
                    module_generation
                )

        self._queue_server_request(
            "workspace/configuration",
            {"items": items},
            on_queued=own_configuration,
        )

    def _server_request_cancelled(self, request_id: str, method: str) -> None:
        super()._server_request_cancelled(request_id, method)
        if method == "workspace/configuration":
            with self._formatting_configuration_lock:
                self._module_search_configuration_requests.pop(request_id, None)

    def _server_request_completed(
        self,
        request_id: str,
        method: str,
        *,
        result: Any,
        error: dict[str, Any] | None,
    ) -> None:
        if (
            method != "workspace/configuration"
            or not self._module_search_configuration_enabled
        ):
            super()._server_request_completed(
                request_id,
                method,
                result=result,
                error=error,
            )
            return

        with self._formatting_configuration_lock:
            module_generation = self._module_search_configuration_requests.pop(
                request_id,
                None,
            )
            formatting_record = self._formatting_configuration_requests.get(
                request_id
            )
            scopes = None if formatting_record is None else formatting_record[1]

        valid_result = (
            isinstance(result, list)
            and scopes is not None
            and len(result) == len(scopes) + 1
        )
        formatting_result = result[:-1] if valid_result else result
        module_value = result[-1] if valid_result else None

        super()._server_request_completed(
            request_id,
            method,
            result=formatting_result,
            error=error,
        )

        if module_generation is None:
            return
        with self._formatting_configuration_lock:
            stale = (
                module_generation != self._module_search_configuration_generation
            )
        if stale:
            self._queue_formatting_configuration()
            return
        if error is not None or not valid_result:
            return

        roots = (
            self._initial_module_search_roots
            if module_value is None
            else self._parse_module_search_root_values(module_value)
        )
        self._apply_module_search_roots(roots)

    def _apply_module_search_roots(
        self,
        roots: tuple[str, ...] | None,
    ) -> None:
        """Install one provider authority and invalidate complete-workspace results."""
        if roots == self._module_search_roots:
            return
        before = self.workspace_symbols.snapshots()
        self._module_search_roots = roots
        self.workspace_symbols.invalidate_complete_queries()
        self._sync_closed_workspace_files()
        self._publish_workspace_diagnostics()
        after = self.workspace_symbols.snapshots()
        self._workspace_scope_changed(before, after)

    def _workspace_semantic_scope_contains(self, uri: str) -> bool:
        """Include configured local module providers without widening folder ownership."""
        if super()._workspace_semantic_scope_contains(uri):
            return True
        roots = self._module_search_roots
        if roots is None:
            return False
        return any(WorkspaceFolderSet._contains(root, uri) for root in roots)

    def _closed_workspace_scan_root_uris(self) -> tuple[str, ...]:
        """Scan workspace roots plus explicit local module-provider roots."""
        base = super()._closed_workspace_scan_root_uris()
        roots = self._module_search_roots
        if roots is None:
            return base
        ordered: list[str] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for root in (*base, *roots):
            identity = WorkspaceFolderSet.uri_identity(root)
            if identity in seen:
                continue
            seen.add(identity)
            ordered.append(root)
        return tuple(ordered)

    def _nova_bare_workspace_resolution(
        self,
        importer_uri: str,
        path: str,
        workspace_uris: tuple[str, ...],
    ) -> NovaModuleResolution:
        roots = self._module_search_roots
        if roots is None:
            return super()._nova_bare_workspace_resolution(
                importer_uri,
                path,
                workspace_uris,
            )

        candidates = self._nova_bare_module_candidates(
            importer_uri,
            path,
            workspace_uris,
            roots,
        )
        if not candidates:
            return NovaModuleResolution("bare")
        return NovaModuleResolution(
            "bare",
            target_uri=candidates[0],
            candidate_uris=candidates,
        )

    def _nova_bare_workspace_import_paths(
        self,
        importer_uri: str,
        target_uri: str,
        workspace_uris: tuple[str, ...],
    ) -> tuple[str, ...]:
        roots = self._module_search_roots
        if roots is None:
            return super()._nova_bare_workspace_import_paths(
                importer_uri,
                target_uri,
                workspace_uris,
            )
        return self._nova_bare_module_import_paths(
            importer_uri,
            target_uri,
            workspace_uris,
            roots,
        )
