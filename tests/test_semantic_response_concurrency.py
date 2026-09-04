from __future__ import annotations

from threading import Event, Thread
from typing import Any

import pytest

from mini_language_server.semantic import Reference
from mini_language_server.server import LanguageServer
from mini_language_server.source import Span
from mini_language_server.symbols import Symbol


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def prepared_server() -> tuple[LanguageServer, str]:
    server = LanguageServer()
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
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


def params_for(method: str, uri: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "textDocument": {"uri": uri},
        "position": {"line": 0, "character": 5},
    }
    if method == "textDocument/references":
        params["context"] = {"includeDeclaration": True}
    elif method == "textDocument/rename":
        params["newName"] = "bar"
    return params


def replace_semantics_without_document_change(server: LanguageServer, uri: str) -> None:
    syntax = server.syntax.get(uri)
    assert syntax is not None
    symbols = server.symbols.publish(syntax, [Symbol("foo", "variable", Span(0, 3))])
    target = symbols.symbols[0]
    server.semantics.publish(symbols, [Reference(Span(4, 7), target)])


@pytest.mark.parametrize(
    "method",
    [
        "textDocument/definition",
        "textDocument/references",
        "textDocument/rename",
    ],
)
def test_same_version_semantic_replacement_suppresses_stale_response(
    method: str, monkeypatch
) -> None:
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
            server.handle(request(method, 41, params_for(method, uri)))
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    replace_semantics_without_document_change(server, uri)
    document = server.documents.get(uri)
    assert document is not None
    assert document.version == 1

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
