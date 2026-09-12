from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer, *, supported: bool = True) -> dict[str, Any]:
    synchronization = {"willSaveWaitUntil": True} if supported else {}
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"synchronization": synchronization}}},
        )
    )
    assert result is not None
    return result


def open_nova(server: NovaProductLanguageServer, uri: str, text: str, version: int = 1) -> None:
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


def will_save(server: NovaProductLanguageServer, uri: str, request_id: int = 2) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/willSaveWaitUntil",
            request_id,
            {"textDocument": {"uri": uri}, "reason": 1},
        )
    )
    assert result is not None
    return result


def test_initialize_negotiates_will_save_wait_until() -> None:
    capabilities = initialize(NovaProductLanguageServer())["result"]["capabilities"]
    assert capabilities["textDocumentSync"] == {
        "openClose": True,
        "change": 2,
        "willSaveWaitUntil": True,
    }


def test_initialize_preserves_incremental_sync_when_unsupported() -> None:
    capabilities = initialize(NovaProductLanguageServer(), supported=False)["result"]["capabilities"]
    assert capabilities["textDocumentSync"] == 2


def test_will_save_returns_deterministic_trivia_aware_formatting_edit() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    source = "fn main() {\nlet x = \"}\"\nif true {\nreturn x\n}\n}\n"
    open_nova(server, uri, source)

    result = will_save(server, uri)["result"]

    assert result == [
        {
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 6, "character": 0},
            },
            "newText": "fn main() {\n    let x = \"}\"\n    if true {\n        return x\n    }\n}\n",
        }
    ]


def test_will_save_tracks_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    assert will_save(server, uri)["result"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert will_save(server, uri, 3)["result"] == []

    open_nova(server, uri, "fn main() {\nreturn 1\n}\n", version=1)
    assert will_save(server, uri, 4)["result"]


def test_will_save_rejects_same_version_semantic_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    original = server.semantics.get(uri)
    assert original is not None
    real_commit = server.semantics.commit_if_current

    def replace_then_commit(snapshot, callback):
        document = server.documents.get(uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.semantics.replace(replacement, expected=original)
        return real_commit(snapshot, callback)

    server.semantics.commit_if_current = replace_then_commit  # type: ignore[method-assign]

    assert will_save(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_will_save_honors_cancellation_before_publication() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    real_checkpoint = server.requests.checkpoint
    checkpoints = 0

    def cancel_before_second_checkpoint(context) -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 2:
            server.handle(notify("$/cancelRequest", {"id": 9}))
        real_checkpoint(context)

    server.requests.checkpoint = cancel_before_second_checkpoint  # type: ignore[method-assign]

    assert will_save(server, uri, 9) == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
