from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer, properties: list[str] | None) -> dict[str, Any]:
    symbol: dict[str, Any] = {}
    if properties is not None:
        symbol["resolveSupport"] = {"properties": properties}
    result = server.handle(
        request("initialize", 1, {"capabilities": {"workspace": {"symbol": symbol}}})
    )
    assert result is not None
    return result


def open_nova(
    server: NovaProductLanguageServer, uri: str, text: str, *, version: int = 1
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


def workspace_symbols(server: NovaProductLanguageServer, query: str = "run") -> dict[str, Any]:
    result = server.handle(request("workspace/symbol", 2, {"query": query}))
    assert result is not None
    return result


def resolve_symbol(
    server: NovaProductLanguageServer, symbol: dict[str, Any], request_id: int = 3
) -> dict[str, Any]:
    result = server.handle(request("workspaceSymbol/resolve", request_id, symbol))
    assert result is not None
    return result


def test_workspace_symbol_resolve_capability_is_negotiated_only_for_supported_property() -> None:
    server = NovaProductLanguageServer()
    enabled = initialize(server, ["location.range"])
    assert enabled["result"]["capabilities"]["workspaceSymbolProvider"] == {
        "resolveProvider": True
    }

    unsupported_server = NovaProductLanguageServer()
    unsupported = initialize(unsupported_server, ["containerName"])
    assert unsupported["result"]["capabilities"]["workspaceSymbolProvider"] is True


def test_workspace_symbol_location_range_is_resolved_lazily() -> None:
    server = NovaProductLanguageServer()
    initialize(server, ["location.range"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn run() {}\n")

    symbol = workspace_symbols(server)["result"][0]
    assert symbol["name"] == "run"
    assert symbol["location"] == {"uri": uri}
    assert isinstance(symbol["data"]["novaWorkspaceSymbolResolve"], int)

    resolved = resolve_symbol(server, symbol)["result"]
    assert resolved["location"]["uri"] == uri
    assert resolved["location"]["range"] == {
        "start": {"line": 0, "character": 3},
        "end": {"line": 0, "character": 6},
    }


def test_workspace_symbol_resolve_rejects_same_version_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server, ["location.range"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn run() {}\n")
    symbol = workspace_symbols(server)["result"][0]

    original = server.workspace_symbols.get(uri)
    document = server.documents.get(uri)
    assert original is not None and document is not None
    replacement = server.nova_adapter.publish(server, document)
    server.workspace_symbols.replace(replacement, expected=original)

    assert resolve_symbol(server, symbol) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_workspace_symbol_resolve_rejects_close_reopen_same_version() -> None:
    server = NovaProductLanguageServer()
    initialize(server, ["location.range"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn run() {}\n")
    symbol = workspace_symbols(server)["result"][0]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, "fn run() {}\n", version=1)

    assert resolve_symbol(server, symbol) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_workspace_symbol_resolve_rejects_workspace_membership_drift() -> None:
    server = NovaProductLanguageServer()
    initialize(server, ["location.range"])
    open_nova(server, "file:///workspace/main.nova", "fn run() {}\n")
    symbol = workspace_symbols(server)["result"][0]

    open_nova(server, "file:///workspace/other.nova", "fn other() {}\n")

    assert resolve_symbol(server, symbol) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_workspace_symbol_resolve_honors_cancellation_before_publication() -> None:
    server = NovaProductLanguageServer()
    initialize(server, ["location.range"])
    open_nova(server, "file:///workspace/main.nova", "fn run() {}\n")
    symbol = workspace_symbols(server)["result"][0]
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def cancel_on_second_checkpoint(context):
        nonlocal calls
        calls += 1
        if calls == 2:
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_on_second_checkpoint  # type: ignore[method-assign]
    assert resolve_symbol(server, symbol) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
