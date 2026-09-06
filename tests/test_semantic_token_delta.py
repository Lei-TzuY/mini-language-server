from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server.semantic_token_delta import SemanticTokenDeltaMixin
from mini_language_server.source import Span
from mini_language_server.symbols import Symbol
from mini_language_server.typed_arguments import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> dict[str, Any]:
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "semanticTokens": {
                            "requests": {"full": {"delta": True}}
                        }
                    }
                }
            },
        )
    )
    assert response is not None
    return response


def open_document(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notification(
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


def token_params(uri: str, previous_result_id: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"textDocument": {"uri": uri}}
    if previous_result_id is not None:
        params["previousResultId"] = previous_result_id
    return params


def test_semantic_token_delta_is_negotiated_and_full_results_get_result_ids() -> None:
    server = NovaProductLanguageServer()
    response = initialize(server)
    provider = response["result"]["capabilities"]["semanticTokensProvider"]
    assert provider["full"] == {"delta": True}

    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn alpha() {}")
    full = server.handle(request("textDocument/semanticTokens/full", 2, token_params(uri)))
    assert full is not None
    assert full["result"]["data"]
    assert full["result"]["resultId"] == "1"


def test_semantic_token_delta_returns_deterministic_edit_after_change() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn alpha() {}")
    full = server.handle(request("textDocument/semanticTokens/full", 2, token_params(uri)))
    assert full is not None
    previous = full["result"]["resultId"]

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn alpha(value) { value }"}],
            },
        )
    )
    delta = server.handle(
        request(
            "textDocument/semanticTokens/full/delta",
            3,
            token_params(uri, previous),
        )
    )
    assert delta is not None
    assert delta["result"]["resultId"] == "2"
    assert delta["result"]["edits"]
    assert "data" not in delta["result"]


def test_unknown_previous_result_id_falls_back_to_full_tokens() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn alpha(value) { value }")

    response = server.handle(
        request(
            "textDocument/semanticTokens/full/delta",
            2,
            token_params(uri, "missing"),
        )
    )
    assert response is not None
    assert response["result"]["resultId"] == "1"
    assert response["result"]["data"]
    assert "edits" not in response["result"]


def test_close_reopen_invalidates_previous_result_lineage() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn alpha() {}")
    full = server.handle(request("textDocument/semanticTokens/full", 2, token_params(uri)))
    assert full is not None
    previous = full["result"]["resultId"]

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(server, uri, "fn beta(value) { value }")
    response = server.handle(
        request(
            "textDocument/semanticTokens/full/delta",
            3,
            token_params(uri, previous),
        )
    )
    assert response is not None
    assert response["result"]["data"]
    assert "edits" not in response["result"]


def test_same_version_semantic_replacement_suppresses_stale_delta(monkeypatch) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn alpha() {}")
    full = server.handle(request("textDocument/semanticTokens/full", 2, token_params(uri)))
    assert full is not None
    previous = full["result"]["resultId"]

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
            server.handle(
                request(
                    "textDocument/semanticTokens/full/delta",
                    4,
                    token_params(uri, previous),
                )
            )
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    syntax = server.syntax.get(uri)
    assert syntax is not None
    symbols = server.symbols.publish(
        syntax,
        [Symbol("alpha", "function", Span(3, 8))],
    )
    server.semantics.publish(symbols, [])

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 4,
            "error": {"code": -32801, "message": "Content modified"},
        }
    ]


def test_semantic_token_delta_honors_request_cancellation(monkeypatch) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn alpha() {}")
    full = server.handle(request("textDocument/semanticTokens/full", 2, token_params(uri)))
    assert full is not None
    previous = full["result"]["resultId"]

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
            server.handle(
                request(
                    "textDocument/semanticTokens/full/delta",
                    5,
                    token_params(uri, previous),
                )
            )
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notification("$/cancelRequest", {"id": 5}))
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 5,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]


def test_semantic_token_edit_minimizes_shared_prefix_and_suffix() -> None:
    assert SemanticTokenDeltaMixin._semantic_token_edits(
        (0, 0, 5, 8, 0, 0, 6, 4, 12, 0),
        (0, 0, 5, 8, 0, 0, 6, 7, 12, 0),
    ) == [{"start": 7, "deleteCount": 1, "data": [7]}]
