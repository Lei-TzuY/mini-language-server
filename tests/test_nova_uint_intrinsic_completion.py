from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None


def open_nova(server: NovaProductLanguageServer, uri: str, version: int, text: str) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": "nova", "version": version, "text": text}},
        )
    )


def completion_response(
    server: NovaProductLanguageServer, uri: str, request_id: int, text: str
) -> dict[str, Any]:
    marker = text.index("/*cursor*/")
    visible = text.replace("/*cursor*/", "")
    document = server.documents.get(uri)
    assert document is not None
    assert document.text == visible
    result = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": marker}},
        )
    )
    assert result is not None
    return result


def completion_items(server: NovaProductLanguageServer, uri: str, request_id: int, text: str) -> list[dict[str, str]]:
    response = completion_response(server, uri, request_id, text)
    assert "result" in response
    return response["result"]


def test_uint_member_completion_exposes_only_implemented_intrinsics() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    marked = "fn main() -> Unit { UInt::/*cursor*/ return (); }\n"
    open_nova(server, uri, 1, marked.replace("/*cursor*/", ""))

    assert completion_items(server, uri, 2, marked) == [
        {"label": "MIN", "detail": "constant: UInt"},
        {"label": "MAX", "detail": "constant: UInt"},
        {"label": "from", "detail": "fn UInt::from(value: Int) -> UInt"},
    ]


def test_member_completion_filters_prefix_and_int_receiver() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    marked = "fn main() -> Unit { UInt::M/*cursor*/ return (); }\n"
    open_nova(server, uri, 1, marked.replace("/*cursor*/", ""))
    assert completion_items(server, uri, 2, marked) == [
        {"label": "MIN", "detail": "constant: UInt"},
        {"label": "MAX", "detail": "constant: UInt"},
    ]

    changed = "fn main() -> Unit { Int::from_/*cursor*/ return (); }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": changed.replace("/*cursor*/", "")}],
            },
        )
    )
    assert completion_items(server, uri, 3, changed) == [
        {"label": "from_uint", "detail": "fn Int::from_uint(value: UInt) -> Int"}
    ]


def test_member_completion_close_reopen_does_not_reuse_old_receiver() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    uint_marked = "fn main() -> Unit { UInt::/*cursor*/ return (); }\n"
    open_nova(server, uri, 1, uint_marked.replace("/*cursor*/", ""))
    assert {item["label"] for item in completion_items(server, uri, 2, uint_marked)} == {"MIN", "MAX", "from"}

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    int_marked = "fn main() -> Unit { Int::/*cursor*/ return (); }\n"
    open_nova(server, uri, 1, int_marked.replace("/*cursor*/", ""))
    assert completion_items(server, uri, 3, int_marked) == [
        {"label": "from_uint", "detail": "fn Int::from_uint(value: UInt) -> Int"}
    ]


def test_same_version_workspace_replacement_suppresses_stale_member_completion() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    marked = "fn main() -> Unit { UInt::/*cursor*/ return (); }\n"
    open_nova(server, uri, 1, marked.replace("/*cursor*/", ""))
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
    assert completion_response(server, uri, 41, marked) == {
        "jsonrpc": "2.0",
        "id": 41,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_member_completion_honors_cancellation_checkpoint() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    marked = "fn main() -> Unit { UInt::/*cursor*/ return (); }\n"
    open_nova(server, uri, 1, marked.replace("/*cursor*/", ""))

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
    thread = Thread(target=lambda: responses.append(completion_response(server, uri, 42, marked)))
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notify("$/cancelRequest", {"id": 42}))
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {"jsonrpc": "2.0", "id": 42, "error": {"code": -32800, "message": "Request cancelled"}}
    ]
