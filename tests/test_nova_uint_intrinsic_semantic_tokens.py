from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.semantic_tokens import TOKEN_TYPES


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer, *, delta: bool = False) -> None:
    full: bool | dict[str, bool] = {"delta": True} if delta else True
    result = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "semanticTokens": {"requests": {"full": full, "range": True}}
                    }
                }
            },
        )
    )
    assert result is not None


def open_nova(server: NovaProductLanguageServer, uri: str, version: int, text: str) -> None:
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


def full_tokens(server: NovaProductLanguageServer, uri: str, request_id: int) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/semanticTokens/full",
            request_id,
            {"textDocument": {"uri": uri}},
        )
    )
    assert result is not None
    return result


def decode(data: list[int]) -> list[tuple[int, int, int, str]]:
    result: list[tuple[int, int, int, str]] = []
    line = 0
    character = 0
    for index in range(0, len(data), 5):
        delta_line, delta_start, length, token_type, _ = data[index : index + 5]
        line += delta_line
        character = character + delta_start if delta_line == 0 else delta_start
        result.append((line, character, length, TOKEN_TYPES[token_type]))
    return result


def test_numeric_intrinsics_add_type_constant_and_method_semantic_tokens() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() -> Unit {\n"
        "    let low = UInt::MIN;\n"
        "    let high = UInt::MAX;\n"
        "    let unsigned = UInt::from(1);\n"
        "    let signed = Int::from_uint(unsigned);\n"
        "}\n"
    )
    open_nova(server, uri, 1, text)
    response = full_tokens(server, uri, 2)
    tokens = decode(response["result"]["data"])

    expected = {
        (1, 14, 4, "type"),
        (1, 20, 3, "enumMember"),
        (2, 15, 4, "type"),
        (2, 21, 3, "enumMember"),
        (3, 19, 4, "type"),
        (3, 25, 4, "method"),
        (4, 17, 3, "type"),
        (4, 22, 9, "method"),
    }
    assert expected <= set(tokens)


def test_numeric_intrinsic_semantic_tokens_are_trivia_aware_and_range_scoped() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() -> Unit {\n"
        "    // UInt::MAX\n"
        "    let value = UInt::from(1);\n"
        "    let text = \"Int::from_uint\";\n"
        "}\n"
    )
    open_nova(server, uri, 1, text)
    result = server.handle(
        request(
            "textDocument/semanticTokens/range",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 2, "character": 0},
                    "end": {"line": 3, "character": 0},
                },
            },
        )
    )
    assert result is not None
    tokens = decode(result["result"]["data"])
    assert (2, 16, 4, "type") in tokens
    assert (2, 22, 4, "method") in tokens
    assert all(line == 2 for line, _, _, _ in tokens)


def test_numeric_intrinsic_semantic_token_delta_tracks_member_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server, delta=True)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { UInt::MAX }\n")
    first = full_tokens(server, uri, 2)["result"]
    assert isinstance(first.get("resultId"), str)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { UInt::from(1) }\n"}],
            },
        )
    )
    second = server.handle(
        request(
            "textDocument/semanticTokens/full/delta",
            3,
            {
                "textDocument": {"uri": uri},
                "previousResultId": first["resultId"],
            },
        )
    )
    assert second is not None
    assert second["result"]["edits"]


def test_same_version_workspace_replacement_suppresses_stale_intrinsic_tokens() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { UInt::MAX }\n")
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
    assert full_tokens(server, uri, 4) == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_numeric_intrinsic_semantic_tokens_honor_cancellation() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { UInt::MAX }\n")
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
    thread = Thread(target=lambda: responses.append(full_tokens(server, uri, 5)))
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
