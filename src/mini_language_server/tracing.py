"""Language-independent LSP trace wrapper for complete product lifecycles."""

from __future__ import annotations

import json
from typing import Any

_TRACE_VALUES = frozenset({"off", "messages", "verbose"})


class TraceLanguageServerMixin:
    """Wrap a composed language server with standard LSP trace notifications."""

    def __init__(self) -> None:
        super().__init__()
        self._trace_value = "off"

    @property
    def trace_value(self) -> str:
        return self._trace_value

    def handle(self, message: Any) -> dict[str, Any] | None:
        if self._is_set_trace_notification(message):
            self._set_trace_value(message.get("params"))
            return None

        mode = self._trace_value
        if mode != "off":
            self._emit_trace(
                self._describe_inbound(message),
                self._verbose_envelope(message) if mode == "verbose" else None,
            )

        response = super().handle(message)

        if mode != "off" and response is not None:
            self._emit_trace(
                self._describe_outbound_response(response),
                self._verbose_envelope(response) if mode == "verbose" else None,
            )
        return response

    def _queue_notification(self, method: str, params: Any = None) -> None:
        super()._queue_notification(method, params)
        if method == "$/logTrace" or self._trace_value == "off":
            return
        self._emit_trace(
            f"server -> client notification {method}",
            (
                self._verbose_value({"method": method, "params": params})
                if self._trace_value == "verbose"
                else None
            ),
        )

    def _queue_server_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> str:
        request_id = super()._queue_server_request(method, params)
        if self._trace_value != "off":
            self._emit_trace(
                f"server -> client request {method} id={request_id}",
                (
                    self._verbose_value(
                        {
                            "id": request_id,
                            "method": method,
                            "params": params,
                        }
                    )
                    if self._trace_value == "verbose"
                    else None
                ),
            )
        return request_id

    @staticmethod
    def _is_set_trace_notification(message: Any) -> bool:
        return (
            isinstance(message, dict)
            and message.get("jsonrpc") == "2.0"
            and message.get("method") == "$/setTrace"
            and "id" not in message
        )

    def _set_trace_value(self, params: Any) -> None:
        if not isinstance(params, dict):
            return
        value = params.get("value")
        if isinstance(value, str) and value in _TRACE_VALUES:
            self._trace_value = value

    def _emit_trace(self, message: str, verbose: str | None = None) -> None:
        params: dict[str, Any] = {"message": message}
        if verbose is not None:
            params["verbose"] = verbose
        # Bypass _queue_notification deliberately: tracing the trace would recurse.
        self._notifications.append(
            {
                "jsonrpc": "2.0",
                "method": "$/logTrace",
                "params": params,
            }
        )

    @classmethod
    def _describe_inbound(cls, message: Any) -> str:
        if not isinstance(message, dict):
            return "client -> server malformed message"
        method = message.get("method")
        if isinstance(method, str):
            if "id" in message:
                return (
                    f"client -> server request {method} "
                    f"id={cls._display_id(message.get('id'))}"
                )
            return f"client -> server notification {method}"

        if "id" in message:
            request_id = cls._display_id(message.get("id"))
            error = message.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                return f"client -> server error response id={request_id} code={code}"
            if "result" in message:
                return f"client -> server response id={request_id}"
        return "client -> server malformed message"

    @classmethod
    def _describe_outbound_response(cls, response: dict[str, Any]) -> str:
        request_id = cls._display_id(response.get("id"))
        error = response.get("error")
        if isinstance(error, dict):
            return (
                f"server -> client error response id={request_id} "
                f"code={error.get('code')}"
            )
        return f"server -> client response id={request_id}"

    @staticmethod
    def _display_id(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        if value is None:
            return "null"
        return "<invalid>"

    @classmethod
    def _verbose_envelope(cls, message: Any) -> str:
        return cls._verbose_value(message)

    @classmethod
    def _verbose_value(cls, value: Any) -> str:
        return json.dumps(
            cls._summarize_value(value),
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _summarize_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            summary: dict[str, Any] = {"type": "object", "keys": sorted(str(key) for key in value)}
            for key in ("jsonrpc", "method", "id"):
                item = value.get(key)
                if isinstance(item, str | int) and not isinstance(item, bool):
                    summary[key] = item
                elif item is None and key in value:
                    summary[key] = None
            if "params" in value:
                summary["params"] = cls._value_shape(value["params"])
            if "result" in value:
                summary["result"] = cls._value_shape(value["result"])
            error = value.get("error")
            if isinstance(error, dict):
                error_summary: dict[str, Any] = {
                    "type": "object",
                    "keys": sorted(str(key) for key in error),
                }
                code = error.get("code")
                if isinstance(code, int) and not isinstance(code, bool):
                    error_summary["code"] = code
                summary["error"] = error_summary
            return summary
        return cls._value_shape(value)

    @classmethod
    def _value_shape(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "boolean"}
        if isinstance(value, str):
            return {"type": "string", "length": len(value)}
        if isinstance(value, int | float):
            return {"type": "number"}
        if isinstance(value, list):
            return {"type": "array", "length": len(value)}
        if isinstance(value, dict):
            return {"type": "object", "keys": sorted(str(key) for key in value)}
        return {"type": type(value).__name__}
