from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.semantic import Reference


def request(
    method: str,
    request_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None
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


def hover(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    token: str,
    request_id: int,
) -> dict[str, Any]:
    offset = text.rindex(token)
    result = server.handle(
        request(
            "textDocument/hover",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": offset},
            },
        )
    )
    assert result is not None
    return result


def test_typed_parameter_and_alias_local_hover_expose_bounded_types() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: String) { input let alias = input alias }\n"
    open_nova(server, uri, text)

    parameter_offset = text.index("input", text.index("{"))
    parameter = server.handle(
        request(
            "textDocument/hover",
            2,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": parameter_offset},
            },
        )
    )
    assert parameter is not None
    assert parameter["result"]["contents"]["value"] == "parameter input: String"

    local = hover(server, uri, text, "alias", 3)
    assert local["result"]["contents"]["value"] == "variable alias: String"


def test_literal_local_hover_and_unknown_expression_fallback_are_deterministic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(source) { let flag = true flag "
        "let unknown = source + 1 unknown }\n"
    )
    open_nova(server, uri, text)

    second_flag = text.index("flag", text.index("flag") + 1)
    flag = server.handle(
        request(
            "textDocument/hover",
            2,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": second_flag},
            },
        )
    )
    assert flag is not None
    assert flag["result"]["contents"]["value"] == "variable flag: Bool"

    unknown = hover(server, uri, text, "unknown", 3)
    assert unknown["result"]["contents"]["value"] == "variable unknown"


def test_typed_local_hover_recomputes_after_change_and_close_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    first = 'fn main() { let value = "text" value }\n'
    open_nova(server, uri, first)
    first_hover = hover(server, uri, first, "value", 2)
    assert first_hover["result"]["contents"]["value"] == "variable value: String"

    second = "fn main() { let value = 1 value }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": second}],
            },
        )
    )
    second_hover = hover(server, uri, second, "value", 3)
    assert second_hover["result"]["contents"]["value"] == "variable value: Int"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    third = "fn main() { let value = false value }\n"
    open_nova(server, uri, third)
    third_hover = hover(server, uri, third, "value", 4)
    assert third_hover["result"]["contents"]["value"] == "variable value: Bool"


def test_same_version_semantic_replacement_suppresses_stale_typed_hover(
    monkeypatch,
) -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 1 value }\n"
    open_nova(server, uri, text)

    entered = Event()
    release = Event()
    responses: list[dict[str, Any] | None] = []
    original = server.semantics.commit_if_current

    def blocked_commit(semantic, commit):
        entered.set()
        assert release.wait(timeout=5)
        return original(semantic, commit)

    monkeypatch.setattr(server.semantics, "commit_if_current", blocked_commit)
    thread = Thread(
        target=lambda: responses.append(hover(server, uri, text, "value", 41))
    )
    thread.start()
    assert entered.wait(timeout=5)

    current = server.semantics.get(uri)
    syntax = server.syntax.get(uri)
    assert current is not None and syntax is not None
    symbols = server.symbols.publish(syntax, current.symbols.symbols)
    symbols_by_span = {symbol.span: symbol for symbol in symbols.symbols}
    references = [
        Reference(reference.span, symbols_by_span[reference.target.span])
        for reference in current.references
    ]
    server.semantics.publish(symbols, references)

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 41,
            "error": {"code": -32801, "message": "Content modified"},
        }
    ]


def test_typed_hover_honors_cancellation_checkpoint(monkeypatch) -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: String) { input }\n"
    open_nova(server, uri, text)

    entered = Event()
    release = Event()
    responses: list[dict[str, Any] | None] = []
    original = server.requests.checkpoint
    blocked = False

    def blocked_checkpoint(context):
        nonlocal blocked
        if not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        return original(context)

    monkeypatch.setattr(server.requests, "checkpoint", blocked_checkpoint)
    offset = text.index("input", text.index("{"))
    thread = Thread(
        target=lambda: responses.append(
            server.handle(
                request(
                    "textDocument/hover",
                    42,
                    {
                        "textDocument": {"uri": uri},
                        "position": {"line": 0, "character": offset},
                    },
                )
            )
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notify("$/cancelRequest", {"id": 42}))
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 42,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
