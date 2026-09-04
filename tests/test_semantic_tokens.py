from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server.semantic import Reference
from mini_language_server.semantic_tokens import TOKEN_TYPES
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


def token_params(uri: str) -> dict[str, Any]:
    return {"textDocument": {"uri": uri}}


def prepared_server() -> tuple[LanguageServer, str]:
    server = LanguageServer()
    server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "semanticTokens": {"requests": {"full": True}}
                    }
                }
            },
        )
    )
    uri = "file:///workspace/main.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "alpha\nbeta gamma",
                }
            },
        )
    )
    document = server.documents.get(uri)
    assert document is not None
    syntax = server.syntax.publish(document, tree=("module",))
    symbols = server.symbols.publish(
        syntax,
        [
            Symbol("alpha", "variable", Span(0, 5)),
            Symbol("beta", "function", Span(6, 10)),
            Symbol("gamma", "custom-kind", Span(11, 16)),
        ],
    )
    server.semantics.publish(symbols, [Reference(Span(11, 16), symbols.symbols[0])])
    return server, uri


def test_semantic_tokens_are_negotiated_and_delta_encoded() -> None:
    server, uri = prepared_server()
    initialize = LanguageServer().handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "semanticTokens": {"requests": {"full": True}}
                    }
                }
            },
        )
    )
    assert initialize is not None
    provider = initialize["result"]["capabilities"]["semanticTokensProvider"]
    assert provider == {
        "legend": {"tokenTypes": list(TOKEN_TYPES), "tokenModifiers": []},
        "full": True,
    }

    variable = TOKEN_TYPES.index("variable")
    function = TOKEN_TYPES.index("function")
    assert server.handle(
        request("textDocument/semanticTokens/full", 2, token_params(uri))
    ) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "data": [
                0,
                0,
                5,
                variable,
                0,
                1,
                0,
                4,
                function,
                0,
                0,
                5,
                5,
                variable,
                0,
            ]
        },
    }


def test_semantic_tokens_are_not_advertised_without_client_support() -> None:
    initialize = LanguageServer().handle(request("initialize", 1, {"capabilities": {}}))
    assert initialize is not None
    assert "semanticTokensProvider" not in initialize["result"]["capabilities"]


def test_semantic_tokens_return_empty_without_current_semantics() -> None:
    server = LanguageServer()
    server.handle(request("initialize", 1))
    uri = "file:///workspace/empty.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "foo",
                }
            },
        )
    )
    assert server.handle(
        request("textDocument/semanticTokens/full", 2, token_params(uri))
    ) == {"jsonrpc": "2.0", "id": 2, "result": {"data": []}}


def test_same_version_semantic_replacement_suppresses_stale_tokens(monkeypatch) -> None:
    server, uri = prepared_server()
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
        target=lambda: responses.append(
            server.handle(request("textDocument/semanticTokens/full", 41, token_params(uri)))
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    syntax = server.syntax.get(uri)
    assert syntax is not None
    symbols = server.symbols.publish(syntax, [Symbol("delta", "variable", Span(0, 5))])
    server.semantics.publish(symbols, [])

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
    assert len(server.requests) == 0


def test_document_change_suppresses_stale_empty_tokens(monkeypatch) -> None:
    server = LanguageServer()
    server.handle(request("initialize", 1))
    uri = "file:///workspace/empty.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "foo",
                }
            },
        )
    )

    entered = Event()
    release = Event()
    responses: list[dict[str, Any] | None] = []
    original = server.requests.checkpoint
    calls = 0

    def blocked_checkpoint(context):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(timeout=5)
        return original(context)

    monkeypatch.setattr(server.requests, "checkpoint", blocked_checkpoint)
    thread = Thread(
        target=lambda: responses.append(
            server.handle(request("textDocument/semanticTokens/full", 42, token_params(uri)))
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "bar"}],
            },
        )
    )

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 42,
            "error": {"code": -32801, "message": "Content modified"},
        }
    ]
    assert len(server.requests) == 0
