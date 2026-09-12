"""Exact-snapshot inlay-hint resolve for the final Nova product."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .code_action_resolve import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticError, SemanticSnapshot
from .server import ServerState
from .workspace import WorkspaceIndexError


@dataclass(frozen=True, slots=True)
class _InlayHintResolveRecord:
    semantic: SemanticSnapshot
    workspace: tuple[SemanticSnapshot, ...]
    hint: dict[str, Any]


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with negotiated, exact-snapshot inlay-hint resolve."""

    def __init__(self) -> None:
        super().__init__()
        self._inlay_hint_resolve_properties: frozenset[str] = frozenset()
        self._inlay_hint_resolve_next = 1
        self._inlay_hint_resolve_records: dict[int, _InlayHintResolveRecord] = {}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            self._inlay_hint_resolve_properties = (
                self._client_inlay_hint_resolve_properties(message.get("params"))
            )
            result = super().handle(message)
            if (
                self._inlay_hint_resolve_properties
                and result is not None
                and isinstance(result.get("result"), dict)
            ):
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict) and capabilities.get(
                    "inlayHintProvider"
                ) is not None:
                    capabilities["inlayHintProvider"] = {"resolveProvider": True}
            return result

        if (
            method == "inlayHint/resolve"
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._inlay_hint_resolve_properties
        ):
            return self._handle_inlay_hint_resolve(
                message.get("id"), message.get("params")
            )

        if (
            method == "textDocument/inlayHint"
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._inlay_hint_resolve_properties
        ):
            return self._handle_lazy_inlay_hint(message)

        return super().handle(message)

    def _handle_lazy_inlay_hint(
        self, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        request_id = message.get("id")
        params = message.get("params")
        uri = self._document_uri(params)
        semantic = self.semantics.get(uri) if uri is not None else None
        snapshots = self.workspace_symbols.snapshots()

        response = super().handle(message)
        if (
            semantic is None
            or response is None
            or not isinstance(response.get("result"), list)
        ):
            return response

        def enrich() -> dict[str, Any]:
            for hint in response["result"]:
                if not isinstance(hint, dict):
                    continue
                token = self._inlay_hint_resolve_next
                self._inlay_hint_resolve_next += 1
                stored = dict(hint)
                data = stored.get("data")
                data = dict(data) if isinstance(data, dict) else {}
                data["novaInlayHintResolve"] = token
                stored["data"] = data
                hint["data"] = dict(data)
                if "tooltip" in self._inlay_hint_resolve_properties:
                    hint.pop("tooltip", None)
                if "textEdits" in self._inlay_hint_resolve_properties:
                    hint.pop("textEdits", None)
                self._inlay_hint_resolve_records[token] = _InlayHintResolveRecord(
                    semantic,
                    snapshots,
                    stored,
                )
            while len(self._inlay_hint_resolve_records) > 256:
                oldest = min(self._inlay_hint_resolve_records)
                del self._inlay_hint_resolve_records[oldest]
            return response

        try:
            return self.semantics.commit_if_current(
                semantic,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, enrich
                ),
            )
        except (SemanticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")

    def _handle_inlay_hint_resolve(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        data = params.get("data")
        if not isinstance(data, dict):
            return self._error(request_id, -32602, "Invalid params")
        token = data.get("novaInlayHintResolve")
        if not isinstance(token, int) or isinstance(token, bool):
            return self._error(request_id, -32602, "Invalid params")
        record = self._inlay_hint_resolve_records.get(token)
        if record is None:
            return self._error(request_id, -32602, "Invalid params")

        if self.semantics.get(record.semantic.uri) is not record.semantic:
            return self._error(request_id, -32801, "Content modified")

        try:
            context = self.requests.start(request_id, uri=record.semantic.uri)
        except RequestError:
            return self._error(request_id, -32801, "Content modified")

        try:
            self.requests.checkpoint(context)
            resolved = dict(params)
            if "tooltip" in self._inlay_hint_resolve_properties:
                resolved["tooltip"] = self._inlay_hint_tooltip(record.hint)
            if "textEdits" in self._inlay_hint_resolve_properties:
                text_edits = record.hint.get("textEdits")
                if isinstance(text_edits, list):
                    resolved["textEdits"] = text_edits

            def commit() -> dict[str, Any]:
                self.requests.checkpoint(context)
                return self._result(request_id, resolved)

            return self.semantics.commit_if_current(
                record.semantic,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    record.workspace, commit
                ),
            )
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except (StaleRequest, SemanticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _inlay_hint_tooltip(hint: dict[str, Any]) -> dict[str, str]:
        label = hint.get("label")
        if not isinstance(label, str):
            label = "inlay hint"
        stripped = label.strip()
        if hint.get("kind") == 2 and stripped.endswith(":"):
            name = stripped[:-1]
            value = f"Nova parameter-name hint for `{name}`."
        elif hint.get("kind") == 1 and stripped.startswith("->"):
            type_name = stripped[2:].strip()
            value = f"Nova inferred return type `{type_name}`."
        elif hint.get("kind") == 1 and stripped.startswith(":"):
            type_name = stripped[1:].strip()
            value = f"Nova inferred local type `{type_name}`."
        else:
            value = f"Nova inlay hint `{stripped}`."
        return {"kind": "markdown", "value": value}

    @staticmethod
    def _client_inlay_hint_resolve_properties(params: Any) -> frozenset[str]:
        if not isinstance(params, dict):
            return frozenset()
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return frozenset()
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return frozenset()
        inlay_hint = text_document.get("inlayHint")
        if not isinstance(inlay_hint, dict):
            return frozenset()
        resolve_support = inlay_hint.get("resolveSupport")
        if not isinstance(resolve_support, dict):
            return frozenset()
        properties = resolve_support.get("properties")
        if not isinstance(properties, list):
            return frozenset()
        supported = {"tooltip", "textEdits"}
        return frozenset(
            value for value in properties if isinstance(value, str) and value in supported
        )
