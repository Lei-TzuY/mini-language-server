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


def test_adapter_parses_function_declarations_and_calls_deterministically() -> None:
    parsed = NovaFunctionAdapter.parse("fn foo() {}\nfn main() { foo() }\n")
    assert [(name, span.start, span.end) for name, span in parsed.declarations] == [
        ("foo", 3, 6),
        ("main", 15, 19),
    ]
    assert [(name, span.start, span.end) for name, span in parsed.calls] == [
        ("foo", 24, 27)
    ]


def test_nova_open_drives_existing_navigation_hover_completion_and_tokens() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn foo() {}\nfn main() { foo() }\n")

    document = server.documents.get(uri)
    syntax = server.syntax.get(uri)
    symbols = server.symbols.get(uri)
    semantics = server.semantics.get(uri)
    assert document is not None
    assert syntax is not None and syntax.document is document
    assert symbols is not None and symbols.syntax is syntax
    assert semantics is not None and semantics.symbols is symbols

    position = {"line": 1, "character": 13}
    definition = server.handle(
        request(
            "textDocument/definition",
            2,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert definition == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "uri": uri,
            "range": {
                "start": {"line": 0, "character": 3},
                "end": {"line": 0, "character": 6},
            },
        },
    }

    hover = server.handle(
        request(
            "textDocument/hover",
            3,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert hover is not None
    assert hover["result"]["contents"]["value"] == "function foo"

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
    assert len(references["result"]) == 2

    completion = server.handle(
        request(
            "textDocument/completion",
            5,
            {"textDocument": {"uri": uri}, "position": position},
        )
    )
    assert completion is not None
    assert completion["result"] == [
        {"label": "foo", "detail": "function"},
        {"label": "main", "detail": "function"},
    ]

    tokens = server.handle(
        request(
            "textDocument/semanticTokens/full",
            6,
            {"textDocument": {"uri": uri}},
        )
    )
    assert tokens is not None
    assert tokens["result"]["data"]


def test_nova_did_change_rebuilds_exact_snapshot_chain_and_invalidates_old_names() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn foo() {}\nfn main() { foo() }\n")
    old_semantics = server.semantics.get(uri)
    assert old_semantics is not None

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn bar() {}\nfn main() { bar() }\n"}],
            },
        )
    )

    current = server.semantics.get(uri)
    assert current is not None
    assert current is not old_semantics
    assert current.version == 2
    assert current.symbols.syntax.document is server.documents.get(uri)
    assert [symbol.name for symbol in current.symbols.symbols] == ["bar", "main"]

    result = server.handle(
        request(
            "textDocument/definition",
            7,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 13},
            },
        )
    )
    assert result is not None
    assert result["result"]["range"]["start"] == {"line": 0, "character": 3}


def test_duplicate_function_names_do_not_create_ambiguous_references() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/duplicate.nova"
    open_nova(server, uri, 1, "fn foo() {}\nfn foo() {}\nfn main() { foo() }\n")

    semantics = server.semantics.get(uri)
    assert semantics is not None
    assert len(semantics.references) == 0


def test_non_nova_documents_are_not_analyzed_by_nova_adapter() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.txt"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "plaintext",
                    "version": 1,
                    "text": "fn foo() {}",
                }
            },
        )
    )
    assert server.syntax.get(uri) is None
    assert server.symbols.get(uri) is None
    assert server.semantics.get(uri) is None
