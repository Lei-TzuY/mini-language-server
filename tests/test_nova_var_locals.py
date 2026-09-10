from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server.nova import NovaFunctionAdapter, NovaLanguageServer
from mini_language_server.semantic import Reference


def request(
    method: str,
    request_id: int,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaLanguageServer) -> None:
    response = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert response is not None


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


def test_adapter_treats_var_as_a_scoped_local_declaration() -> None:
    parsed = NovaFunctionAdapter.parse(
        "fn main(input) { input var value = input value }\n"
    )

    assert [(item.owner.start, item.name, item.span.start) for item in parsed.locals] == [
        (3, "value", 27)
    ]
    assert [item.span.start for item in parsed.parameter_references] == [17, 35]
    assert [(item.name, item.span.start) for item in parsed.local_references] == [
        ("value", 41)
    ]
    assert all(item.name != "var" for item in parsed.unresolved_names)


def test_var_local_drives_definition_references_and_rename() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/var-local.nova"
    text = "fn main() { var value = 1 value }\n"
    open_nova(server, uri, 1, text)

    semantics = server.semantics.get(uri)
    assert semantics is not None
    assert [(symbol.name, symbol.kind) for symbol in semantics.symbols.symbols] == [
        ("main", "function"),
        ("value", "variable"),
    ]
    actual_references = [
        (reference.span.start, reference.target.span.start)
        for reference in semantics.references
    ]
    assert actual_references == [(26, 16)]

    position = {"line": 0, "character": 27}
    definition = server.handle(
        request(
            "textDocument/definition",
            2,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert definition is not None
    assert definition["result"]["range"] == {
        "start": {"line": 0, "character": 16},
        "end": {"line": 0, "character": 21},
    }

    references = server.handle(
        request(
            "textDocument/references",
            3,
            {
                "textDocument": {"uri": uri},
                "position": position,
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert [item["range"]["start"]["character"] for item in references["result"]] == [
        16,
        26,
    ]

    rename = server.handle(
        request(
            "textDocument/rename",
            4,
            {
                "textDocument": {"uri": uri},
                "position": position,
                "newName": "item",
            },
        )
    )
    assert rename is not None
    edits = rename["result"]["changes"][uri]
    assert [edit["range"]["start"]["character"] for edit in edits] == [16, 26]
    assert all(edit["newText"] == "item" for edit in edits)


def test_var_local_change_close_and_reopen_replace_parent_identity() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/var-reopen.nova"
    open_nova(server, uri, 1, "fn main() { var value = 1 value }\n")
    first = server.semantics.get(uri)
    assert first is not None

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { var item = 1 item }\n"}],
            },
        )
    )
    changed = server.semantics.get(uri)
    assert changed is not None and changed is not first
    assert [symbol.name for symbol in changed.symbols.symbols] == ["main", "item"]
    assert changed.references[0].target is changed.symbols.symbols[1]

    server.handle(
        notification("textDocument/didClose", {"textDocument": {"uri": uri}})
    )
    open_nova(server, uri, 2, "fn main() { var value = 1 value }\n")
    reopened = server.semantics.get(uri)
    assert reopened is not None and reopened is not changed
    assert reopened.symbols.syntax.document is server.documents.get(uri)
    assert [symbol.name for symbol in reopened.symbols.symbols] == ["main", "value"]


def test_same_version_var_semantic_replacement_suppresses_stale_definition(
    monkeypatch,
) -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/stale-var.nova"
    open_nova(server, uri, 1, "fn main() { var value = 1 value }\n")

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
                        "position": {"line": 0, "character": 27},
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
