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
    result = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "completion": {},
                        "semanticTokens": {"requests": {"full": True}},
                    }
                }
            },
        )
    )
    assert result is not None


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


def test_adapter_parses_scoped_locals_and_parameter_shadowing() -> None:
    parsed = NovaFunctionAdapter.parse(
        "fn main(input) { input let value = input value let input = value input }\n"
    )

    assert [(item.owner.start, item.name, item.span.start) for item in parsed.locals] == [
        (3, "value", 27),
        (3, "input", 51),
    ]
    assert [item.span.start for item in parsed.parameter_references] == [17, 35]
    assert [(item.name, item.span.start) for item in parsed.local_references] == [
        ("value", 41),
        ("value", 59),
        ("input", 65),
    ]


def test_local_uses_drive_navigation_hover_completion_rename_and_tokens() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/locals.nova"
    text = "fn main(input) { input let value = input value }\n"
    open_nova(server, uri, 1, text)

    semantics = server.semantics.get(uri)
    assert semantics is not None
    assert [(symbol.name, symbol.kind) for symbol in semantics.symbols.symbols] == [
        ("main", "function"),
        ("input", "parameter"),
        ("value", "variable"),
    ]
    actual_references = [
        (reference.span.start, reference.target.kind)
        for reference in semantics.references
    ]
    assert actual_references == [
        (17, "parameter"),
        (35, "parameter"),
        (41, "variable"),
    ]

    position = {"line": 0, "character": 42}
    definition = server.handle(
        request(
            "textDocument/definition",
            2,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert definition is not None
    assert definition["result"]["range"] == {
        "start": {"line": 0, "character": 27},
        "end": {"line": 0, "character": 32},
    }

    hover = server.handle(
        request(
            "textDocument/hover",
            3,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert hover is not None
    assert hover["result"]["contents"]["value"] == "variable value"

    references = server.handle(
        request(
            "textDocument/references",
            4,
            {
                "textDocument": {"uri": uri},
                "position": position,
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert [item["range"]["start"]["character"] for item in references["result"]] == [
        27,
        41,
    ]

    completion = server.handle(
        request(
            "textDocument/completion",
            5,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert completion is not None
    assert {item["label"]: item["detail"] for item in completion["result"]} == {
        "input": "parameter",
        "main": "function",
        "value": "variable",
    }

    prepared = server.handle(
        request(
            "textDocument/prepareRename",
            6,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert prepared is not None
    assert prepared["result"] == {
        "range": {
            "start": {"line": 0, "character": 27},
            "end": {"line": 0, "character": 32},
        },
        "placeholder": "value",
    }

    rename = server.handle(
        request(
            "textDocument/rename",
            7,
            {
                "textDocument": {"uri": uri},
                "position": position,
                "newName": "item",
            },
        )
    )
    assert rename is not None
    edits = rename["result"]["changes"][uri]
    assert [edit["range"]["start"]["character"] for edit in edits] == [27, 41]
    assert all(edit["newText"] == "item" for edit in edits)

    tokens = server.handle(
        request(
            "textDocument/semanticTokens/full",
            8,
            {"textDocument": {"uri": uri}},
        )
    )
    assert tokens is not None
    token_types = tokens["result"]["data"][3::5]
    assert 7 in token_types  # LSP parameter token type
    assert 8 in token_types  # LSP variable token type
    assert 12 in token_types  # LSP function token type


def test_duplicate_locals_are_diagnostic_and_do_not_resolve_late_use() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/duplicate-locals.nova"
    open_nova(server, uri, 1, "fn main() { let value = 1 let value = 2 value }\n")

    semantics = server.semantics.get(uri)
    diagnostics = server.diagnostics.get(uri)
    assert semantics is not None
    assert diagnostics is not None
    assert not semantics.references
    assert [item.code for item in diagnostics.diagnostics] == [
        "nova.duplicate-variable",
        "nova.duplicate-variable",
    ]


def test_did_change_replaces_local_parent_identity_and_scope() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/change-locals.nova"
    open_nova(server, uri, 1, "fn main() { let value = 1 value }\n")
    old = server.semantics.get(uri)
    assert old is not None

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { let item = 1 item }\n"}],
            },
        )
    )

    current = server.semantics.get(uri)
    assert current is not None and current is not old
    assert current.symbols.syntax.document is server.documents.get(uri)
    assert [(symbol.name, symbol.kind) for symbol in current.symbols.symbols] == [
        ("main", "function"),
        ("item", "variable"),
    ]
    assert current.references[0].target is current.symbols.symbols[1]


def test_close_reopen_never_reuses_old_local_snapshot() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/reopen-locals.nova"
    open_nova(server, uri, 1, "fn main() { let value = 1 value }\n")
    old = server.semantics.get(uri)
    assert old is not None

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, 1, "fn main() { let item = 1 item }\n")

    current = server.semantics.get(uri)
    assert current is not None and current is not old
    assert current.symbols.syntax.document is server.documents.get(uri)
    assert [symbol.name for symbol in current.symbols.symbols] == ["main", "item"]


def test_same_version_local_semantic_replacement_suppresses_stale_response(monkeypatch) -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/stale-locals.nova"
    open_nova(server, uri, 1, "fn main() { let value = 1 value }\n")

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
    assert current.version == 1
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
