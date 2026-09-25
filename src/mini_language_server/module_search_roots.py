"""Initialization-authoritative Nova bare-module search roots."""

from __future__ import annotations

import urllib.parse
from typing import Any

from .server import ServerState
from .will_save_formatting import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace_files import WorkspaceUriIdentity
from .workspace_folders import WorkspaceFolderSet
from .workspace_lsp import NovaModuleResolution


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Add bounded precedence-ordered bare-module roots from initialization."""

    def __init__(self) -> None:
        super().__init__()
        self._module_search_root_identities: (
            tuple[WorkspaceUriIdentity, ...] | None
        ) = None

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "initialize"
            and self.state is ServerState.PRE_INITIALIZE
        ):
            self._module_search_root_identities = self._parse_module_search_roots(
                message.get("params")
            )
        return super().handle(message)

    @staticmethod
    def _parse_module_search_roots(
        params: Any,
    ) -> tuple[WorkspaceUriIdentity, ...] | None:
        """Return explicit canonical root order, None when no policy is configured."""
        if not isinstance(params, dict):
            return None
        options = params.get("initializationOptions")
        if not isinstance(options, dict):
            return None
        nova = options.get("nova")
        if not isinstance(nova, dict) or "moduleSearchRoots" not in nova:
            return None
        configured = nova.get("moduleSearchRoots")
        if not isinstance(configured, list):
            return ()

        roots: list[WorkspaceUriIdentity] = []
        seen: set[WorkspaceUriIdentity] = set()
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
            ):
                return ()
            if identity in seen:
                continue
            seen.add(identity)
            roots.append(identity)
        return tuple(roots)

    def _nova_bare_workspace_resolution(
        self,
        importer_uri: str,
        path: str,
        workspace_uris: tuple[str, ...],
    ) -> NovaModuleResolution:
        roots = self._module_search_root_identities
        if roots is None:
            return super()._nova_bare_workspace_resolution(
                importer_uri,
                path,
                workspace_uris,
            )

        active = {
            WorkspaceFolderSet.uri_identity(folder.uri): folder
            for folder in self.workspace_folders.folders()
        }
        folders = tuple(
            active[identity]
            for identity in roots
            if identity in active
        )
        candidates = self._nova_bare_workspace_candidates(
            importer_uri,
            path,
            workspace_uris,
            folders,
        )
        if not candidates:
            return NovaModuleResolution("bare")
        return NovaModuleResolution(
            "bare",
            target_uri=candidates[0],
            candidate_uris=candidates,
        )
