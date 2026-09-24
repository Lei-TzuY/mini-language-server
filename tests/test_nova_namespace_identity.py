from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.semantic_tokens import TOKEN_TYPES


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "documentHighlight": {},
                        "semanticTokens": {
                            "requests": {"full": True},
                            "tokenModifiers": ["declaration"],
                        },
                    }
                }
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    version: int = 1,
) -> None:
    server.handle(
        notify(
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


def position(text: str, marker: str, *, delta: int = 0) -> dict[str, int]:
    offset = text.index(marker) + delta
    line = text.count("\n", 0, offset)
    line_start = text.rfind("\n", 0, offset) + 1
    return {"line": line, "character": offset - line_start}


def location_start(response: dict[str, Any]) -> dict[str, int]:
    return response["result"]["range"]["start"]


def decode_tokens(data: list[int]) -> list[tuple[int, int, int, int, int]]:
    result: list[tuple[int, int, int, int, int]] = []
    line = 0
    character = 0
    for index in range(0, len(data), 5):
        delta_line, delta_start, length, token_type, modifiers = data[index : index + 5]
        line += delta_line
        character = character + delta_start if delta_line == 0 else delta_start
        result.append((line, character, length, token_type, modifiers))
    return result


def namespace_fixture() -> tuple[NovaProductLanguageServer, str, str, str]:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    provider = "fn target() {}\n"
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn main() { api::target(); api::missing(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, caller_uri, caller)
    return server, provider_uri, caller_uri, caller


def test_namespace_definition_and_references_share_importer_identity() -> None:
    server, _, caller_uri, caller = namespace_fixture()

    defined = server.handle(
        request(
            "textDocument/definition",
            10,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "api::target", delta=1),
            },
        )
    )
    assert defined is not None
    assert defined["result"]["uri"] == caller_uri
    assert location_start(defined) == {
        "line": 0,
        "character": len("import * as "),
    }

    refs = server.handle(
        request(
            "textDocument/references",
            11,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "api::target", delta=1),
                "context": {"includeDeclaration": False},
            },
        )
    )
    assert refs is not None
    assert [item["range"]["start"] for item in refs["result"]] == [
        {"line": 1, "character": len("fn main() { ")},
        {
            "line": 1,
            "character": len("fn main() { api::target(); "),
        },
    ]

    refs_with_declaration = server.handle(
        request(
            "textDocument/references",
            12,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "api", delta=1),
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert refs_with_declaration is not None
    assert refs_with_declaration["result"][0]["range"]["start"] == {
        "line": 0,
        "character": len("import * as "),
    }
    assert all(item["uri"] == caller_uri for item in refs_with_declaration["result"])


def test_namespace_highlights_declaration_and_qualifiers() -> None:
    server, _, caller_uri, caller = namespace_fixture()

    response = server.handle(
        request(
            "textDocument/documentHighlight",
            20,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "api::missing", delta=1),
            },
        )
    )
    assert response is not None
    assert [
        (item["range"]["start"], item["kind"])
        for item in response["result"]
    ] == [
        ({"line": 0, "character": len("import * as ")}, 3),
        ({"line": 1, "character": len("fn main() { ")}, 2),
        (
            {
                "line": 1,
                "character": len("fn main() { api::target(); "),
            },
            2,
        ),
    ]


def test_namespace_semantic_tokens_mark_declaration_and_qualifiers() -> None:
    server, _, caller_uri, _ = namespace_fixture()

    response = server.handle(
        request(
            "textDocument/semanticTokens/full",
            30,
            {"textDocument": {"uri": caller_uri}},
        )
    )
    assert response is not None
    namespace_type = TOKEN_TYPES.index("namespace")
    namespace_tokens = [
        item
        for item in decode_tokens(response["result"]["data"])
        if item[3] == namespace_type
    ]
    assert [
        (line, character, length)
        for line, character, length, _, _ in namespace_tokens
    ] == [
        (0, len("import * as "), len("api")),
        (1, len("fn main() { "), len("api")),
        (1, len("fn main() { api::target(); "), len("api")),
    ]
    assert [item[4] for item in namespace_tokens] == [1, 0, 0]


def test_duplicate_namespace_binding_fails_closed_across_identity_surfaces() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    left_uri = "file:///workspace/left.nova"
    right_uri = "file:///workspace/right.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, left_uri, "fn left() {}\n")
    open_nova(server, right_uri, "fn right() {}\n")
    caller = (
        "import * as api from ./left.nova;\n"
        "import * as api from ./right.nova;\n"
        "fn main() { api::left(); }\n"
    )
    open_nova(server, caller_uri, caller)

    definition = server.handle(
        request(
            "textDocument/definition",
            40,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "api::left", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"] is None

    highlights = server.handle(
        request(
            "textDocument/documentHighlight",
            41,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "api::left", delta=1),
            },
        )
    )
    assert highlights is not None
    assert highlights["result"] == []

    tokens = server.handle(
        request(
            "textDocument/semanticTokens/full",
            42,
            {"textDocument": {"uri": caller_uri}},
        )
    )
    assert tokens is not None
    namespace_type = TOKEN_TYPES.index("namespace")
    assert all(
        token_type != namespace_type
        for _, _, _, token_type, _ in decode_tokens(tokens["result"]["data"])
    )


def test_namespace_member_definition_still_targets_provider_function() -> None:
    server, provider_uri, caller_uri, caller = namespace_fixture()

    member = server.handle(
        request(
            "textDocument/definition",
            50,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "target", delta=1),
            },
        )
    )
    assert member is not None
    assert member["result"]["uri"] == provider_uri

    qualifier = server.handle(
        request(
            "textDocument/definition",
            51,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "api::target", delta=1),
            },
        )
    )
    assert qualifier is not None
    assert qualifier["result"]["uri"] == caller_uri


def test_namespace_definition_does_not_depend_on_declaration_source_order() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, provider_uri, "fn target() {}\n")
    caller = (
        "fn main() { api::target(); }\n"
        "import * as api from ./provider.nova;\n"
    )
    open_nova(server, caller_uri, caller)

    response = server.handle(
        request(
            "textDocument/definition",
            60,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "api::target", delta=1),
            },
        )
    )
    assert response is not None
    assert response["result"]["uri"] == caller_uri
    assert response["result"]["range"]["start"] == {
        "line": 1,
        "character": len("import * as "),
    }

    references = server.handle(
        request(
            "textDocument/references",
            61,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "api::target", delta=1),
                "context": {"includeDeclaration": False},
            },
        )
    )
    assert references is not None
    assert [item["range"]["start"] for item in references["result"]] == [
        {"line": 0, "character": len("fn main() { ")}
    ]


def test_namespace_is_published_as_semantic_symbol_and_reference() -> None:
    server, _, caller_uri, caller = namespace_fixture()
    semantics = server.semantics.get(caller_uri)
    assert semantics is not None

    namespaces = [
        symbol
        for symbol in semantics.symbols.symbols
        if symbol.kind == "namespace"
    ]
    assert [(symbol.name, caller[symbol.span.start : symbol.span.end]) for symbol in namespaces] == [
        ("api", "api")
    ]
    namespace = namespaces[0]
    references = [
        reference
        for reference in semantics.references
        if reference.target is namespace
    ]
    assert [
        caller[reference.span.start : reference.span.end]
        for reference in references
    ] == ["api", "api"]


def test_namespace_appears_in_document_and_workspace_symbols() -> None:
    server, _, caller_uri, _ = namespace_fixture()

    document = server.handle(
        request(
            "textDocument/documentSymbol",
            70,
            {"textDocument": {"uri": caller_uri}},
        )
    )
    assert document is not None
    assert any(
        item["name"] == "api" and item["kind"] == 3
        for item in document["result"]
    )

    workspace = server.handle(
        request(
            "workspace/symbol",
            71,
            {"query": "api"},
        )
    )
    assert workspace is not None
    assert any(
        item["name"] == "api"
        and item["kind"] == 3
        and item["location"]["uri"] == caller_uri
        for item in workspace["result"]
    )


def test_duplicate_and_reserved_namespaces_do_not_publish_semantic_symbols() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/caller.nova"
    text = (
        "import * as api from ./left.nova;\n"
        "import * as api from ./right.nova;\n"
        "import * as Int from ./numeric.nova;\n"
        "fn main() { api::left(); Int::from(1); }\n"
    )
    open_nova(server, caller_uri, text)

    semantics = server.semantics.get(caller_uri)
    assert semantics is not None
    assert [
        (symbol.name, symbol.kind)
        for symbol in semantics.symbols.symbols
        if symbol.kind == "namespace"
    ] == []
