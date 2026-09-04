from __future__ import annotations

from typing import Any

from mini_language_server.semantic import Reference
from mini_language_server.server import LanguageServer
from mini_language_server.source import Span
from mini_language_server.symbols import Symbol


def request(method: str, request_id: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def test_hover_is_advertised_and_uses_current_semantic_symbol() -> None:
    server = LanguageServer()
    initialize = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert initialize is not None
    assert initialize["result"]["capabilities"]["hoverProvider"] is True

    uri = "file:///workspace/main.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "let foo = foo\n",
                }
            },
        )
    )
    document = server.documents.get(uri)
    assert document is not None
    syntax = server.syntax.publish(document, tree=("module",))
    symbols = server.symbols.publish(
        syntax,
        [Symbol("foo", "variable", Span(4, 7))],
    )
    target = symbols.symbols[0]
    server.semantics.publish(symbols, [Reference(Span(10, 13), target)])

    hover = server.handle(
        request(
            "textDocument/hover",
            2,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 11},
            },
        )
    )
    assert hover == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "contents": {"kind": "plaintext", "value": "variable foo"},
            "range": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 7},
            },
        },
    }


def test_hover_returns_null_without_current_semantics_or_symbol() -> None:
    server = LanguageServer()
    server.handle(request("initialize", 1))
    uri = "file:///workspace/main.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "foo bar",
                }
            },
        )
    )

    params = {
        "textDocument": {"uri": uri},
        "position": {"line": 0, "character": 1},
    }
    assert server.handle(request("textDocument/hover", 2, params)) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": None,
    }

    document = server.documents.get(uri)
    assert document is not None
    syntax = server.syntax.publish(document, tree=("module",))
    symbols = server.symbols.publish(syntax, [Symbol("bar", "variable", Span(4, 7))])
    server.semantics.publish(symbols, [])
    assert server.handle(request("textDocument/hover", 3, params)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "result": None,
    }
