from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server.nova import NovaFunctionAdapter, NovaLanguageServer
from mini_language_server.semantic import Reference


def request(method: str, request_id: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaLanguageServer) -> None:
    response = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert response is not None and "result" in response


def open_nova(server: NovaLanguageServer, uri: str, version: int, text: str) -> None:
    server.handle(
        notification(
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


def diagnostic_codes(server: NovaLanguageServer, uri: str) -> list[str | None]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item.code for item in snapshot.diagnostics]


def test_parser_tracks_only_truly_unresolved_bare_names() -> None:
    parsed = NovaFunctionAdapter.parse(
        "fn main(input) { missing input before let before = input before }\n"
    )

    assert [(item.name, item.span.start) for item in parsed.unresolved_names] == [
        ("missing", 17),
        ("before", 31),
    ]
    assert [item.name for item in parsed.parameter_references] == ["input", "input"]
    assert [item.name for item in parsed.local_references] == ["before"]


def test_unresolved_name_diagnostic_is_versioned_and_deterministic() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/unresolved-name.nova"
    open_nova(server, uri, 1, "fn main(input) { missing input let value = missing value }\n")

    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    assert [
        (item.code, item.message, item.span.start)
        for item in snapshot.diagnostics
    ] == [
        ("nova.unresolved-name", "unresolved name 'missing'", 17),
        ("nova.unresolved-name", "unresolved name 'missing'", 43),
    ]
    assert snapshot.semantic is server.semantics.get(uri)


def test_did_change_and_close_reopen_replace_unresolved_name_diagnostics() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/unresolved-lifecycle.nova"
    open_nova(server, uri, 1, "fn main() { missing }\n")
    old = server.diagnostics.get(uri)
    assert old is not None
    assert diagnostic_codes(server, uri) == ["nova.unresolved-name"]

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { let missing = 1 missing }\n"}],
            },
        )
    )
    changed = server.diagnostics.get(uri)
    assert changed is not None and changed is not old
    assert diagnostic_codes(server, uri) == []

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, 1, "fn main() { reopened }\n")
    reopened = server.diagnostics.get(uri)
    assert reopened is not None and reopened is not changed
    assert diagnostic_codes(server, uri) == ["nova.unresolved-name"]
    assert reopened.semantic.symbols.syntax.document is server.documents.get(uri)


def test_same_version_replacement_suppresses_stale_unresolved_name_request(monkeypatch) -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/unresolved-stale.nova"
    open_nova(server, uri, 1, "fn main() { missing }\n")

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
                    "textDocument/definition",
                    41,
                    {
                        "textDocument": {"uri": uri},
                        "position": {"line": 0, "character": 13},
                    },
                )
            )
        )
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
    assert len(server.requests) == 0
