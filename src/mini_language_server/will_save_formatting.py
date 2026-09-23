"""Exact-snapshot negotiated will-save formatting for Nova."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .implementation import NovaProductLanguageServer as _PreviousNovaProductLanguageServer
from .server import ServerState
from .source import Span

_DEFAULT_FORMATTING_TAB_SIZE = 4
_DEFAULT_FORMATTING_INSERT_SPACES = True
_FORMATTING_CONFIGURATION_SECTION = "mini-language-server.formatting"
_FORMATTING_CONFIGURATION_REGISTRATION_ID = (
    "mini-language-server.formatting.didChangeConfiguration"
)


class NovaProductLanguageServer(_PreviousNovaProductLanguageServer):
    """Final Nova product with negotiated formatting before save."""

    def __init__(self) -> None:
        super().__init__()
        self._workspace_configuration_support = False
        self._did_change_configuration_dynamic_registration = False
        self._formatting_configuration_registration_attempted = False
        self._formatting_configuration_registration_request: str | None = None
        self._formatting_configuration_registration_active = False
        self._formatting_configurations: dict[
            str | None, tuple[int, bool]
        ] = {
            None: (
                _DEFAULT_FORMATTING_TAB_SIZE,
                _DEFAULT_FORMATTING_INSERT_SPACES,
            )
        }
        self._formatting_configuration_generation = 0
        self._formatting_configuration_requests: dict[
            str, tuple[int, tuple[str | None, ...]]
        ] = {}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            params = message.get("params")
            self._workspace_configuration_support = (
                self._client_supports_workspace_configuration(params)
            )
            self._did_change_configuration_dynamic_registration = (
                self._client_supports_dynamic_configuration_registration(params)
            )

        if method == "initialized" and self.state is ServerState.RUNNING:
            result = super().handle(message)
            if self._workspace_configuration_support:
                self._queue_formatting_configuration_registration()
                self._invalidate_formatting_configuration()
            return result

        if (
            method == "workspace/didChangeConfiguration"
            and "id" not in message
            and self.state is ServerState.RUNNING
        ):
            result = super().handle(message)
            if self._workspace_configuration_support:
                self._invalidate_formatting_configuration()
            return result
        if (
            method == "textDocument/willSaveWaitUntil"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_will_save_wait_until(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and isinstance(result.get("result"), dict)
            and self._client_supports_will_save_wait_until(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["textDocumentSync"] = {
                    "openClose": True,
                    "change": 2,
                    "willSaveWaitUntil": True,
                }
        return result

    @staticmethod
    def _client_supports_workspace_configuration(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        return (
            isinstance(workspace, dict)
            and workspace.get("configuration") is True
        )

    @staticmethod
    def _client_supports_dynamic_configuration_registration(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        configuration = workspace.get("didChangeConfiguration")
        return (
            isinstance(configuration, dict)
            and configuration.get("dynamicRegistration") is True
        )

    def _queue_formatting_configuration_registration(self) -> None:
        if (
            not self._workspace_configuration_support
            or not self._did_change_configuration_dynamic_registration
            or self._formatting_configuration_registration_attempted
        ):
            return
        self._formatting_configuration_registration_attempted = True
        request_id = self._queue_server_request(
            "client/registerCapability",
            {
                "registrations": [
                    {
                        "id": _FORMATTING_CONFIGURATION_REGISTRATION_ID,
                        "method": "workspace/didChangeConfiguration",
                        "registerOptions": {
                            "section": _FORMATTING_CONFIGURATION_SECTION,
                        },
                    }
                ]
            },
        )
        if request_id is None:
            return
        self._formatting_configuration_registration_request = request_id

    def _workspace_folder_scope_changed(self, before: Any, after: Any) -> None:
        super()._workspace_folder_scope_changed(before, after)
        if self._workspace_configuration_support:
            self._invalidate_formatting_configuration()

    def _invalidate_formatting_configuration(self) -> None:
        self._formatting_configuration_generation += 1
        self._cancel_pending_server_requests("workspace/configuration")
        self._queue_formatting_configuration()

    def _formatting_configuration_scopes(self) -> tuple[str | None, ...]:
        return (
            None,
            *(folder.uri for folder in self.workspace_folders.folders()),
        )

    def _queue_formatting_configuration(self) -> None:
        if (
            not self._workspace_configuration_support
            or self._has_pending_server_request("workspace/configuration")
        ):
            return
        scopes = self._formatting_configuration_scopes()
        items = []
        for scope in scopes:
            item: dict[str, Any] = {
                "section": _FORMATTING_CONFIGURATION_SECTION
            }
            if scope is not None:
                item["scopeUri"] = scope
            items.append(item)
        request_id = self._queue_server_request(
            "workspace/configuration",
            {"items": items},
        )
        if request_id is None:
            return
        self._formatting_configuration_requests[request_id] = (
            self._formatting_configuration_generation,
            scopes,
        )

    def _server_request_cancelled(self, request_id: str, method: str) -> None:
        super()._server_request_cancelled(request_id, method)
        if method == "client/registerCapability":
            if request_id == self._formatting_configuration_registration_request:
                self._formatting_configuration_registration_request = None
            return
        if method == "workspace/configuration":
            self._formatting_configuration_requests.pop(request_id, None)

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
        if method == "client/registerCapability":
            if request_id != self._formatting_configuration_registration_request:
                return
            self._formatting_configuration_registration_request = None
            if error is None and result is None:
                self._formatting_configuration_registration_active = True
            return
        if method != "workspace/configuration":
            return
        record = self._formatting_configuration_requests.pop(request_id, None)
        if record is None:
            return
        generation, scopes = record
        if generation != self._formatting_configuration_generation:
            self._queue_formatting_configuration()
            return
        if error is not None:
            return
        if not isinstance(result, list) or len(result) != len(scopes):
            return

        updated: dict[str | None, tuple[int, bool]] = {}
        default = (
            _DEFAULT_FORMATTING_TAB_SIZE,
            _DEFAULT_FORMATTING_INSERT_SPACES,
        )
        for scope, value in zip(scopes, result, strict=True):
            parsed = self._parse_formatting_configuration_value(value)
            if parsed is None:
                parsed = self._formatting_configurations.get(scope, default)
            updated[scope] = parsed
        self._formatting_configurations = updated

    @staticmethod
    def _parse_formatting_configuration_value(
        value: Any,
    ) -> tuple[int, bool] | None:
        if value is None:
            return (
                _DEFAULT_FORMATTING_TAB_SIZE,
                _DEFAULT_FORMATTING_INSERT_SPACES,
            )
        if not isinstance(value, dict):
            return None
        tab_size = value.get("tabSize", _DEFAULT_FORMATTING_TAB_SIZE)
        insert_spaces = value.get(
            "insertSpaces",
            _DEFAULT_FORMATTING_INSERT_SPACES,
        )
        if (
            isinstance(tab_size, bool)
            or not isinstance(tab_size, int)
            or tab_size <= 0
            or not isinstance(insert_spaces, bool)
        ):
            return None
        return tab_size, insert_spaces

    def _formatting_settings_for_uri(self, uri: str) -> tuple[int, bool]:
        default = (
            _DEFAULT_FORMATTING_TAB_SIZE,
            _DEFAULT_FORMATTING_INSERT_SPACES,
        )
        scope = self.workspace_folders.scope_uri_for(uri)
        if scope is not None and scope in self._formatting_configurations:
            return self._formatting_configurations[scope]
        return self._formatting_configurations.get(None, default)

    @staticmethod
    def _client_supports_will_save_wait_until(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        synchronization = text_document.get("synchronization")
        return isinstance(synchronization, dict) and synchronization.get(
            "willSaveWaitUntil"
        ) is True

    def _handle_will_save_wait_until(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            uri = self._document_uri(params)
            assert uri is not None
            self.requests.checkpoint(context)
            document = self.documents.get(uri)
            if document is None or document.language_id != self.nova_adapter.language_id:
                self.requests.checkpoint(context)
                return self._result(request_id, [])
            semantics = self.semantics.get(uri)
            if semantics is None or semantics.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            tab_size, insert_spaces = self._formatting_settings_for_uri(uri)
            formatted = self._format_nova_document(
                document.text,
                tab_size=tab_size,
                insert_spaces=insert_spaces,
            )
            self.requests.checkpoint(context)
            if formatted == document.text:
                return self._current_semantic_result(semantics, request_id, [])

            source = self._source_text(document.text)
            edits = [
                {
                    "range": self._range(source, Span(0, len(document.text))),
                    "newText": formatted,
                }
            ]
            return self._current_semantic_result(semantics, request_id, edits)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
