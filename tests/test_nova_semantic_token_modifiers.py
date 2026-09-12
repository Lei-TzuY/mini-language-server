from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.semantic_tokens import TOKEN_TYPES


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer, *, modifiers: list[str]
) -> list[str]:
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "semanticTokens": {
                            "requests": {"full": True, "range": True},
                            "tokenModifiers": modifiers,
                        }
                    }
                }
            },
        )
    )
    assert response is not None
    provider = response["result"]["capabilities"]["semanticTokensProvider"]
    return provider["legend"]["tokenModifiers"]


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notify(
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


def semantic_tokens(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    *,
    source_range: dict[str, Any] | None = None,
) -> dict[str, Any]:
    method = (
        "textDocument/semanticTokens/range"
        if source_range is not None
        else "textDocument/semanticTokens/full"
    )
    params: dict[str, Any] = {"textDocument": {"uri": uri}}
    if source_range is not None:
        params["range"] = source_range
    result = server.handle(request(method, request_id, params))
    assert result is not None
    return result


def decode(
    data: list[int], modifier_legend: list[str]
) -> list[tuple[int, int, int, str, frozenset[str]]]:
    result: list[tuple[int, int, int, str, frozenset[str]]] = []
    line = 0
    character = 0
    for index in range(0, len(data), 5):
        delta_line, delta_start, length, token_type, modifiers = data[index : index + 5]
        line += delta_line
        character = character + delta_start if delta_line == 0 else delta_start
        names = frozenset(
            name
            for bit, name in enumerate(modifier_legend)
            if modifiers & (1 << bit)
        )
        result.append((line, character, length, TOKEN_TYPES[token_type], names))
    return result


def test_nova_reference_tokens_and_mutability_modifiers_are_negotiated() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(
        server,
        modifiers=["modification", "readonly", "declaration", "static"],
    )
    assert legend == ["declaration", "readonly", "modification"]
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(input: Int) {\n"
        "    let fixed = input;\n"
        "    var mutable = fixed;\n"
        "    mutable = fixed;\n"
        "}\n"
    )
    open_nova(server, uri, text)

    response = semantic_tokens(server, uri, 2)
    tokens = set(decode(response["result"]["data"], legend))
    declaration = frozenset({"declaration"})
    immutable = frozenset({"declaration", "readonly"})
    readonly = frozenset({"readonly"})
    modification = frozenset({"modification"})

    assert (0, 3, 4, "function", declaration) in tokens
    assert (0, 8, 5, "parameter", declaration) in tokens
    assert (1, 8, 5, "variable", immutable) in tokens
    assert (1, 16, 5, "parameter", frozenset()) in tokens
    assert (2, 8, 7, "variable", declaration) in tokens
    assert (2, 18, 5, "variable", readonly) in tokens
    assert (3, 4, 7, "variable", modification) in tokens
    assert (3, 14, 5, "variable", readonly) in tokens


def test_invalid_immutable_assignment_is_readonly_and_modification() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, modifiers=["readonly", "modification"])
    assert legend == ["readonly", "modification"]
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { let fixed = 1; fixed = 2; }\n")

    response = semantic_tokens(server, uri, 2)
    tokens = set(decode(response["result"]["data"], legend))
    assert (0, 27, 5, "variable", frozenset({"readonly", "modification"})) in tokens


def test_reference_tokens_exist_without_modifier_support() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, modifiers=[])
    assert legend == []
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn helper() {}\nfn main() { helper(); }\n")

    response = semantic_tokens(server, uri, 2)
    tokens = set(decode(response["result"]["data"], legend))
    assert (1, 12, 6, "function", frozenset()) in tokens
    assert all(not modifiers for *_, modifiers in tokens)


def test_reference_tokens_and_modifiers_respect_range_requests() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(
        server, modifiers=["declaration", "readonly", "modification"]
    )
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() {\n"
        "    let fixed = 1;\n"
        "    var mutable = fixed;\n"
        "    mutable = fixed;\n"
        "}\n"
    )
    open_nova(server, uri, text)

    response = semantic_tokens(
        server,
        uri,
        2,
        source_range={
            "start": {"line": 3, "character": 0},
            "end": {"line": 4, "character": 0},
        },
    )
    tokens = decode(response["result"]["data"], legend)
    assert tokens == [
        (3, 4, 7, "variable", frozenset({"modification"})),
        (3, 14, 5, "variable", frozenset({"readonly"})),
    ]


def test_same_version_workspace_replacement_rejects_stale_reference_tokens() -> None:
    server = NovaProductLanguageServer()
    initialize(server, modifiers=["declaration", "readonly", "modification"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { let value = 1; value; }\n")
    original = server.workspace_symbols.get(uri)
    assert original is not None
    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    assert semantic_tokens(server, uri, 4) == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_reference_semantic_tokens_honor_cancellation() -> None:
    server = NovaProductLanguageServer()
    initialize(server, modifiers=["declaration", "readonly", "modification"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { let value = 1; value; }\n")
    entered = Event()
    release = Event()
    responses: list[dict[str, Any] | None] = []
    original = server.requests.checkpoint
    blocked = False

    def blocked_checkpoint(context):
        nonlocal blocked
        if not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        return original(context)

    server.requests.checkpoint = blocked_checkpoint  # type: ignore[method-assign]
    thread = Thread(
        target=lambda: responses.append(semantic_tokens(server, uri, 5))
    )
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notify("$/cancelRequest", {"id": 5}))
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
