from __future__ import annotations

from typing import Any

from mini_language_server.nova import NovaFunctionAdapter, NovaLanguageServer


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


def test_adapter_parses_scoped_parameters_and_body_references() -> None:
    parsed = NovaFunctionAdapter.parse(
        "fn first(value) { value }\nfn second(value) { value }\n"
    )
    assert [(item.owner.start, item.name, item.span.start) for item in parsed.parameters] == [
        (3, "value", 9),
        (27, "value", 34),
    ]
    assert [
        (item.owner.start, item.name, item.span.start)
        for item in parsed.parameter_references
    ] == [
        (3, "value", 17),
        (27, "value", 42),
    ]


def test_parameter_uses_drive_navigation_hover_completion_rename_and_tokens() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/params.nova"
    text = "fn greet(name) { name }\nfn main() { greet() }\n"
    open_nova(server, uri, 1, text)

    semantics = server.semantics.get(uri)
    assert semantics is not None
    assert [(symbol.name, symbol.kind) for symbol in semantics.symbols.symbols] == [
        ("greet", "function"),
        ("name", "parameter"),
        ("main", "function"),
    ]
    assert len(semantics.references) == 2
    assert semantics.references[0].target.kind == "parameter"
    assert semantics.references[0].target.name == "name"

    position = {"line": 0, "character": 18}
    definition = server.handle(
        request(
            "textDocument/definition",
            2,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert definition is not None
    assert definition["result"]["range"] == {
        "start": {"line": 0, "character": 9},
        "end": {"line": 0, "character": 13},
    }

    hover = server.handle(
        request(
            "textDocument/hover",
            3,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert hover is not None
    assert hover["result"]["contents"]["value"] == "parameter name"

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
        9,
        17,
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
        "greet": "function",
        "main": "function",
        "name": "parameter",
    }

    rename = server.handle(
        request(
            "textDocument/rename",
            6,
            {
                "textDocument": {"uri": uri},
                "position": position,
                "newName": "person",
            },
        )
    )
    assert rename is not None
    edits = rename["result"]["changes"][uri]
    assert [edit["range"]["start"]["character"] for edit in edits] == [9, 17]
    assert all(edit["newText"] == "person" for edit in edits)

    tokens = server.handle(
        request(
            "textDocument/semanticTokens/full",
            7,
            {"textDocument": {"uri": uri}},
        )
    )
    assert tokens is not None
    data = tokens["result"]["data"]
    token_types = data[3::5]
    assert 7 in token_types  # LSP parameter token type
    assert 12 in token_types  # LSP function token type


def test_duplicate_parameters_are_diagnostic_and_do_not_resolve_body_use() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/duplicate-params.nova"
    open_nova(server, uri, 1, "fn bad(value, value) { value }\n")

    semantics = server.semantics.get(uri)
    diagnostics = server.diagnostics.get(uri)
    assert semantics is not None
    assert diagnostics is not None
    assert not semantics.references
    assert [item.code for item in diagnostics.diagnostics] == [
        "nova.duplicate-parameter",
        "nova.duplicate-parameter",
    ]


def test_did_change_replaces_parameter_parent_identity_and_scope() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/change-params.nova"
    open_nova(server, uri, 1, "fn greet(name) { name }\n")
    old = server.semantics.get(uri)
    assert old is not None

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn greet(person) { person }\n"}],
            },
        )
    )

    current = server.semantics.get(uri)
    assert current is not None and current is not old
    assert current.symbols.syntax.document is server.documents.get(uri)
    assert [(symbol.name, symbol.kind) for symbol in current.symbols.symbols] == [
        ("greet", "function"),
        ("person", "parameter"),
    ]
    assert current.references[0].target is current.symbols.symbols[1]


def test_close_reopen_never_reuses_old_parameter_snapshot() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/reopen-params.nova"
    open_nova(server, uri, 1, "fn greet(name) { name }\n")
    old = server.semantics.get(uri)
    assert old is not None

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, 1, "fn greet(person) { person }\n")

    current = server.semantics.get(uri)
    assert current is not None and current is not old
    assert current.symbols.syntax.document is server.documents.get(uri)
    assert [symbol.name for symbol in current.symbols.symbols] == ["greet", "person"]
