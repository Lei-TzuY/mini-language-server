from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server.semantic import Reference, SemanticSnapshot
from mini_language_server.server import LanguageServer
from mini_language_server.source import Span
from mini_language_server.symbols import Symbol


def request(method: str, request_id: int = 1, params: object | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: object | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def prepared_server() -> tuple[LanguageServer, str]:
    server = LanguageServer()
    server.handle(request("initialize"))
    uri = "file:///workspace/main.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "foo foo",
                }
            },
        )
    )
    document = server.documents.get(uri)
    assert document is not None
    syntax = server.syntax.publish(document, tree=object())
    symbols = server.symbols.publish(syntax, [Symbol("foo", "variable", Span(0, 3))])
    target = symbols.symbols[0]
    server.semantics.publish(symbols, [Reference(Span(4, 7), target)])
    return server, uri


def definition_params(uri: str) -> dict[str, Any]:
    return {
        "textDocument": {"uri": uri},
        "position": {"line": 0, "character": 5},
    }


def run_blocked_definition(
    server: LanguageServer,
    uri: str,
    monkeypatch,
) -> tuple[Event, Event, list[dict[str, Any] | None], Thread]:
    entered = Event()
    release = Event()
    responses: list[dict[str, Any] | None] = []
    original = SemanticSnapshot.definition_at

    def blocked_definition(self: SemanticSnapshot, offset: int):
        entered.set()
        assert release.wait(timeout=5)
        return original(self, offset)

    monkeypatch.setattr(SemanticSnapshot, "definition_at", blocked_definition)
    thread = Thread(
        target=lambda: responses.append(
            server.handle(
                request(
                    "textDocument/definition",
                    request_id=41,
                    params=definition_params(uri),
                )
            )
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    return entered, release, responses, thread


def test_cancel_request_suppresses_inflight_definition(monkeypatch) -> None:
    server, uri = prepared_server()
    _, release, responses, thread = run_blocked_definition(server, uri, monkeypatch)

    assert server.handle(notification("$/cancelRequest", {"id": 41})) is None
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 41,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
    assert len(server.requests) == 0


def test_document_change_suppresses_inflight_stale_result(monkeypatch) -> None:
    server, uri = prepared_server()
    _, release, responses, thread = run_blocked_definition(server, uri, monkeypatch)

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "bar bar"}],
            },
        )
    )
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


def test_unknown_and_malformed_cancel_notifications_are_harmless() -> None:
    server, _ = prepared_server()

    assert server.handle(notification("$/cancelRequest", {"id": 999})) is None
    assert server.handle(notification("$/cancelRequest", {})) is None
    assert server.handle(notification("$/cancelRequest", {"id": None})) is None
    assert server.handle(notification("$/cancelRequest", {"id": ""})) is None
    assert len(server.requests) == 0


def test_boolean_cancel_id_does_not_alias_integer_request(monkeypatch) -> None:
    server, uri = prepared_server()
    _, release, responses, thread = run_blocked_definition(server, uri, monkeypatch)

    assert server.handle(notification("$/cancelRequest", {"id": True})) is None
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses[0] is not None
    assert "result" in responses[0]
    assert responses[0]["id"] == 41


def test_cancel_after_completion_does_not_poison_reused_id() -> None:
    server, uri = prepared_server()
    params = definition_params(uri)

    first = server.handle(request("textDocument/definition", request_id=7, params=params))
    assert first is not None and "result" in first
    assert server.handle(notification("$/cancelRequest", {"id": 7})) is None
    second = server.handle(request("textDocument/definition", request_id=7, params=params))

    assert second == first
    assert len(server.requests) == 0
