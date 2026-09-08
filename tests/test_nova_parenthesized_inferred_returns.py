from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    return server


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": text,
                }
            },
        )
    )


def hover_value(server: NovaProductLanguageServer, uri: str, text: str) -> str:
    offset = text.rindex("value")
    response = server.handle(
        request(
            "textDocument/hover",
            10,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": offset},
            },
        )
    )
    assert response is not None
    return response["result"]["contents"]["value"]


def test_nested_parenthesized_literal_and_call_returns_infer_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn source() { return ((1)); } "
        "fn wrapper() { return (source()); } "
        "fn main() { let value = wrapper() value }\n"
    )
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value: Int"


def test_parenthesized_compound_return_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn source() -> Int { return 1; } "
        "fn wrapper() { return (source() + 1); } "
        "fn main() { let value = wrapper() value }\n"
    )
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value"
