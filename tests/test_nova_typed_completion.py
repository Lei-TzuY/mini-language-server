from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None


def open_nova(
    server: NovaProductLanguageServer, uri: str, version: int, text: str
) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": version,
                    "text": text,
                }
            },
        )
    )


def complete(
    server: NovaProductLanguageServer, uri: str, request_id: int
) -> dict[str, str]:
    result = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}},
        )
    )
    assert result is not None
    return {item["label"]: item["detail"] for item in result["result"]}


def test_completion_exposes_bounded_parameter_literal_and_alias_types() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        1,
        'fn main(input: String) { let count = 1 let alias = input alias }\n',
    )

    details = complete(server, uri, 2)
    assert details["input"] == "parameter: String"
    assert details["count"] == "variable: Int"
    assert details["alias"] == "variable: String"
    assert details["main"] == "function"


def test_completion_recomputes_types_after_change_and_keeps_unknown_fallback() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { let value = 1 value }\n")
    assert complete(server, uri, 2)["value"] == "variable: Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { let value = other + 1 value }\n"}],
            },
        )
    )
    assert complete(server, uri, 3)["value"] == "variable"


def test_completion_close_reopen_does_not_reuse_old_type() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main(input: Bool) { input }\n")
    assert complete(server, uri, 2)["input"] == "parameter: Bool"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, 1, "fn main(input) { input }\n")
    assert complete(server, uri, 3)["input"] == "parameter"
