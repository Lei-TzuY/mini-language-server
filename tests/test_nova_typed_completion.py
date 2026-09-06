from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None


def open_nova(
    server: NovaProductLanguageServer, uri: str, version: int, text: str
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


def completion_response(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    *,
    character: int | None = None,
) -> dict[str, Any]:
    if character is None:
        document = server.documents.get(uri)
        assert document is not None
        character = document.text.rfind("}")
        assert character >= 0
    result = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": character},
            },
        )
    )
    assert result is not None
    return result


def complete(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    *,
    character: int | None = None,
) -> dict[str, str]:
    result = completion_response(server, uri, request_id, character=character)
    return {item["label"]: item["detail"] for item in result["result"]}


def test_completion_exposes_bounded_parameter_literal_and_alias_types() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        1,
        'fn main(input: String) { let count = 1 let alias = input alias }\n',
    )

    details = complete(server, uri, 2)
    assert details["input"] == "parameter: String"
    assert details["count"] == "variable: Int"
    assert details["alias"] == "variable: String"
    assert details["main"] == "fn main(input: String)"


def test_completion_respects_function_scope_and_local_declaration_order() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn first(left: Int) { let hidden = 1 hidden } "
        "fn second(right: String) { let visible = right visible let later = 1 }\n"
    )
    open_nova(server, uri, 1, text)
    cursor = text.index(" let later")

    details = complete(server, uri, 2, character=cursor)
    assert details["right"] == "parameter: String"
    assert details["visible"] == "variable: String"
    assert "left" not in details
    assert "hidden" not in details
    assert "later" not in details
    assert details["first"] == "fn first(left: Int)"
    assert details["second"] == "fn second(right: String)"


def test_completion_recomputes_types_after_change_and_keeps_unknown_fallback() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { let value = 1 value }\n")
    assert complete(server, uri, 2)["value"] == "variable: Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { let value = other + 1 value }\n"}],
            },
        )
    )
    assert complete(server, uri, 3)["value"] == "variable"


def test_completion_close_reopen_does_not_reuse_old_type() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main(input: Bool) { input }\n")
    assert complete(server, uri, 2)["input"] == "parameter: Bool"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, 1, "fn main(input) { input }\n")
    assert complete(server, uri, 3)["input"] == "parameter"


def test_same_version_semantic_replacement_suppresses_stale_typed_completion() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main(input: String) { input }\n")
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
    assert completion_response(server, uri, 41) == {
        "jsonrpc": "2.0",
        "id": 41,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_typed_completion_honors_cancellation_checkpoint() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main(input: String) { input }\n")

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
        target=lambda: responses.append(completion_response(server, uri, 42))
    )
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notify("$/cancelRequest", {"id": 42}))
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 42,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
