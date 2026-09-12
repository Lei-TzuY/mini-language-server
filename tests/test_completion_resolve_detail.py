from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize_params(*properties: str) -> dict[str, Any]:
    return {
        "capabilities": {
            "textDocument": {
                "completion": {
                    "completionItem": {
                        "resolveSupport": {"properties": list(properties)}
                    }
                }
            }
        }
    }


def open_nova(server: NovaProductLanguageServer, uri: str, *, version: int = 1) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": version,
                    "text": (
                        "fn helper(value: Int) -> Int { return value; }\n"
                        "fn main() -> Int { helper(1); return 0; }\n"
                    ),
                }
            },
        )
    )


def helper_completion(server: NovaProductLanguageServer, uri: str) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/completion",
            2,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 18},
            },
        )
    )
    assert response is not None
    return next(item for item in response["result"] if item["label"] == "helper")


def test_detail_only_resolve_lazily_restores_completion_detail() -> None:
    server = NovaProductLanguageServer()
    initialized = server.handle(request("initialize", 1, initialize_params("detail")))
    assert initialized is not None
    assert initialized["result"]["capabilities"]["completionProvider"] == {
        "resolveProvider": True
    }

    uri = "file:///workspace/main.nova"
    open_nova(server, uri)
    item = helper_completion(server, uri)
    assert "detail" not in item
    assert item["data"]["novaCompletionResolve"] >= 1

    resolved = server.handle(request("completionItem/resolve", 3, item))
    assert resolved is not None
    assert resolved["result"]["detail"] == "fn helper(value: Int) -> Int"
    assert "documentation" not in resolved["result"]


def test_detail_and_documentation_resolve_from_same_captured_item() -> None:
    server = NovaProductLanguageServer()
    server.handle(
        request("initialize", 1, initialize_params("detail", "documentation"))
    )
    uri = "file:///workspace/main.nova"
    open_nova(server, uri)
    item = helper_completion(server, uri)
    assert "detail" not in item

    resolved = server.handle(request("completionItem/resolve", 3, item))
    assert resolved is not None
    assert resolved["result"]["detail"] == "fn helper(value: Int) -> Int"
    assert resolved["result"]["documentation"] == {
        "kind": "markdown",
        "value": "```nova\nfn helper(value: Int) -> Int\n```",
    }


def test_unsupported_resolve_property_does_not_enable_resolve_provider() -> None:
    server = NovaProductLanguageServer()
    initialized = server.handle(request("initialize", 1, initialize_params("sortText")))
    assert initialized is not None
    assert initialized["result"]["capabilities"]["completionProvider"] == {
        "resolveProvider": False
    }


def test_detail_resolve_rejects_close_reopen_same_version() -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params("detail")))
    uri = "file:///workspace/main.nova"
    open_nova(server, uri)
    item = helper_completion(server, uri)

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, version=1)

    assert server.handle(request("completionItem/resolve", 3, item)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }
