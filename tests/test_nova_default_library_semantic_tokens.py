from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.semantic_tokens import TOKEN_TYPES


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer, modifiers: list[str]) -> list[str]:
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


def test_default_library_is_negotiated_for_implemented_numeric_intrinsics() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(
        server,
        ["defaultLibrary", "readonly", "declaration", "documentation"],
    )
    assert legend == ["declaration", "readonly", "defaultLibrary"]
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() -> Unit {\n"
        "    let low = UInt::MIN;\n"
        "    let high = UInt::MAX;\n"
        "    let value = UInt::from(1);\n"
        "    let signed = Int::from_uint(value);\n"
        "}\n"
    )
    open_nova(server, uri, text)

    response = semantic_tokens(server, uri, 2)
    tokens = set(decode(response["result"]["data"], legend))
    default_library = frozenset({"defaultLibrary"})
    assert (1, 14, 4, "type", default_library) in tokens
    assert (1, 20, 3, "enumMember", default_library) in tokens
    assert (2, 15, 4, "type", default_library) in tokens
    assert (2, 21, 3, "enumMember", default_library) in tokens
    assert (3, 16, 4, "type", default_library) in tokens
    assert (3, 22, 4, "method", default_library) in tokens
    assert (4, 17, 3, "type", default_library) in tokens
    assert (4, 22, 9, "method", default_library) in tokens


def test_default_library_falls_back_when_client_does_not_support_it() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, ["declaration"])
    assert legend == ["declaration"]
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Unit { let value = UInt::MAX; }\n")

    response = semantic_tokens(server, uri, 2)
    tokens = decode(response["result"]["data"], legend)
    assert (0, 32, 4, "type", frozenset()) in tokens
    assert (0, 38, 3, "enumMember", frozenset()) in tokens


def test_default_library_modifier_respects_range_requests() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, ["defaultLibrary"])
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() -> Unit {\n"
        "    let low = UInt::MIN;\n"
        "    let high = UInt::MAX;\n"
        "}\n"
    )
    open_nova(server, uri, text)

    response = semantic_tokens(
        server,
        uri,
        2,
        source_range={
            "start": {"line": 2, "character": 0},
            "end": {"line": 3, "character": 0},
        },
    )
    tokens = decode(response["result"]["data"], legend)
    assert tokens == [
        (2, 8, 4, "variable", frozenset()),
        (2, 15, 4, "type", frozenset({"defaultLibrary"})),
        (2, 21, 3, "enumMember", frozenset({"defaultLibrary"})),
    ]


def test_same_version_workspace_replacement_rejects_stale_default_library_tokens() -> None:
    server = NovaProductLanguageServer()
    initialize(server, ["defaultLibrary"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Unit { let value = UInt::MAX; }\n")
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


def test_default_library_semantic_tokens_honor_cancellation() -> None:
    server = NovaProductLanguageServer()
    initialize(server, ["defaultLibrary"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Unit { let value = UInt::MAX; }\n")
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
