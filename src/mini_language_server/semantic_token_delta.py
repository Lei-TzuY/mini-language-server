"""Language-independent semantic-token delta composition."""

from __future__ import annotations

from threading import RLock
from typing import Any


class SemanticTokenDeltaMixin:
    """Add negotiated LSP semantic-token deltas around exact full-token requests.

    The underlying server remains responsible for producing and freshness-gating full
    semantic-token results. This mixin only retains the latest successfully published
    token data per URI and computes a deterministic edit against the client-provided
    ``previousResultId``. Unknown lineage falls back to a full token result, as allowed
    by LSP.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._semantic_token_delta_enabled = False
        self._semantic_token_result_counter = 0
        self._semantic_token_results: dict[str, tuple[str, tuple[int, ...]]] = {}
        self._semantic_token_result_lock = RLock()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")

        if method == "initialize":
            self._semantic_token_delta_enabled = self._client_supports_semantic_tokens_delta(
                message.get("params")
            )
            response = super().handle(message)
            if self._semantic_token_delta_enabled and response is not None:
                result = response.get("result")
                if isinstance(result, dict):
                    capabilities = result.get("capabilities")
                    if isinstance(capabilities, dict):
                        provider = capabilities.get("semanticTokensProvider")
                        if isinstance(provider, dict) and provider.get("full") is True:
                            provider["full"] = {"delta": True}
            return response

        if (
            self._semantic_token_delta_enabled
            and method == "textDocument/semanticTokens/full"
        ):
            response = super().handle(message)
            return self._attach_semantic_token_result(message, response)

        if (
            self._semantic_token_delta_enabled
            and method == "textDocument/semanticTokens/full/delta"
        ):
            return self._handle_semantic_token_delta(message)

        response = super().handle(message)
        if method == "textDocument/didClose":
            uri = self._semantic_token_uri(message.get("params"))
            if uri is not None:
                with self._semantic_token_result_lock:
                    self._semantic_token_results.pop(uri, None)
        return response

    def _handle_semantic_token_delta(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = message.get("id")
        params = message.get("params")
        uri = self._semantic_token_uri(params)
        if uri is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        previous_result_id = params.get("previousResultId")
        if not isinstance(previous_result_id, str) or not previous_result_id:
            return self._error(request_id, -32602, "Invalid params")

        full_message = dict(message)
        full_message["method"] = "textDocument/semanticTokens/full"
        response = super().handle(full_message)
        if response is None or "error" in response:
            assert response is not None
            return response
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            return self._error(request_id, -32603, "Invalid semantic token result")
        data = result["data"]
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in data):
            return self._error(request_id, -32603, "Invalid semantic token result")

        current = tuple(data)
        with self._semantic_token_result_lock:
            previous = self._semantic_token_results.get(uri)
            result_id = self._next_semantic_token_result_id_locked()
            self._semantic_token_results[uri] = (result_id, current)

        if previous is None or previous[0] != previous_result_id:
            return self._result(request_id, {"resultId": result_id, "data": data})

        edits = self._semantic_token_edits(previous[1], current)
        return self._result(request_id, {"resultId": result_id, "edits": edits})

    def _attach_semantic_token_result(
        self, message: dict[str, Any], response: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if response is None or "error" in response:
            return response
        uri = self._semantic_token_uri(message.get("params"))
        result = response.get("result")
        if uri is None or not isinstance(result, dict) or not isinstance(result.get("data"), list):
            return response
        data = result["data"]
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in data):
            return response
        with self._semantic_token_result_lock:
            result_id = self._next_semantic_token_result_id_locked()
            self._semantic_token_results[uri] = (result_id, tuple(data))
        result["resultId"] = result_id
        return response

    def _next_semantic_token_result_id_locked(self) -> str:
        self._semantic_token_result_counter += 1
        return str(self._semantic_token_result_counter)

    @staticmethod
    def _semantic_token_edits(
        previous: tuple[int, ...], current: tuple[int, ...]
    ) -> list[dict[str, Any]]:
        prefix = 0
        limit = min(len(previous), len(current))
        while prefix < limit and previous[prefix] == current[prefix]:
            prefix += 1

        previous_suffix = len(previous)
        current_suffix = len(current)
        while (
            previous_suffix > prefix
            and current_suffix > prefix
            and previous[previous_suffix - 1] == current[current_suffix - 1]
        ):
            previous_suffix -= 1
            current_suffix -= 1

        if prefix == len(previous) and prefix == len(current):
            return []

        edit: dict[str, Any] = {
            "start": prefix,
            "deleteCount": previous_suffix - prefix,
        }
        replacement = list(current[prefix:current_suffix])
        if replacement:
            edit["data"] = replacement
        return [edit]

    @classmethod
    def _client_supports_semantic_tokens_delta(cls, params: Any) -> bool:
        requests = cls._semantic_tokens_requests(params)
        if requests is None:
            return False
        full = requests.get("full")
        return isinstance(full, dict) and full.get("delta") is True

    @staticmethod
    def _semantic_token_uri(params: Any) -> str | None:
        if not isinstance(params, dict):
            return None
        text_document = params.get("textDocument")
        if not isinstance(text_document, dict):
            return None
        uri = text_document.get("uri")
        return uri if isinstance(uri, str) and uri else None
