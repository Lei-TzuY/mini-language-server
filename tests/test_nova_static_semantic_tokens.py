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


def tokens(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    source_range: dict[str, Any] | None = None,
) -> dict[str, Any]:
    method = "textDocument/semanticTokens/range" if source_range else "textDocument/semanticTokens/full"
    params: dict[str, Any] = {"textDocument": {"uri": uri}}
    if source_range is not None:
        params["range"] = source_range
    response = server.handle(request(method, request_id, params))
    assert response is not None
    return response


def decode(data: list[int], legend: list[str]) -> list[tuple[int, int, int, str, frozenset[str]]]:
    result = []
    line = 0
    character = 0
    for index in range(0, len(data), 5):
        delta_line, delta_start, length, token_type, modifiers = data[index : index + 5]
        line += delta_line
        character = character + delta_start if delta_line == 0 else delta_start
        names = frozenset(name for bit, name in enumerate(legend) if modifiers & (1 << bit))
        result.append((line, character, length, TOKEN_TYPES[token_type], names))
    return result


def test_static_modifier_marks_only_implemented_associated_members() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, ["static", "defaultLibrary", "documentation"])
    assert legend == ["defaultLibrary", "static"]
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() -> Unit {\n"
        "    let low = UInt::MIN;\n"
        "    let value = UInt::from(1);\n"
        "    let signed = Int::from_uint(value);\n"
        "}\n"
    )
    open_nova(server, uri, text)
    response = tokens(server, uri, 2)
    decoded = set(decode(response["result"]["data"], legend))
    library = frozenset({"defaultLibrary"})
    static_library = frozenset({"defaultLibrary", "static"})
    assert (1, 14, 4, "type", library) in decoded
    assert (1, 20, 3, "enumMember", static_library) in decoded
    assert (2, 16, 4, "type", library) in decoded
    assert (2, 22, 4, "method", static_library) in decoded
    assert (3, 17, 3, "type", library) in decoded
    assert (3, 22, 9, "method", static_library) in decoded


def test_static_modifier_falls_back_when_unsupported() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, ["defaultLibrary"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Unit { let value = UInt::MAX; }\n")
    decoded = decode(tokens(server, uri, 2)["result"]["data"], legend)
    assert (0, 38, 3, "enumMember", frozenset({"defaultLibrary"})) in decoded


def test_static_modifier_respects_range_requests() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, ["static"])
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main() -> Unit {\n    let low = UInt::MIN;\n    let high = UInt::MAX;\n}\n",
    )
    response = tokens(
        server,
        uri,
        3,
        {"start": {"line": 2, "character": 0}, "end": {"line": 3, "character": 0}},
    )
    decoded = decode(response["result"]["data"], legend)
    assert (2, 21, 3, "enumMember", frozenset({"static"})) in decoded
    assert all(item[0] == 2 for item in decoded)


def test_same_version_replacement_rejects_stale_static_tokens() -> None:
    server = NovaProductLanguageServer()
    initialize(server, ["static"])
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
    assert tokens(server, uri, 4) == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_static_semantic_tokens_honor_cancellation() -> None:
    server = NovaProductLanguageServer()
    initialize(server, ["static"])
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
    thread = Thread(target=lambda: responses.append(tokens(server, uri, 5)))
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notify("$/cancelRequest", {"id": 5}))
    release.set()
    thread.join(timeout=5)
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 5,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
