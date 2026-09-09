from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None
    return server


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


def hover(server: NovaProductLanguageServer, uri: str, request_id: int) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/hover",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 15},
            },
        )
    )
    assert result is not None
    return result


def test_inferred_return_type_surfaces_in_cross_file_hover() -> None:
    server = initialized_server()
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/library.nova", "fn target() { return 1; }\n")
    open_nova(server, main_uri, "fn caller() { target() }\n")

    response = hover(server, main_uri, 2)
    assert response["result"]["contents"]["value"] == "fn target() -> Int"


def test_explicit_return_annotation_wins_and_ambiguous_inference_is_suppressed() -> None:
    server = initialized_server()
    main_uri = "file:///workspace/main.nova"
    library_uri = "file:///workspace/library.nova"
    open_nova(
        server,
        library_uri,
        'fn target() -> String { return "x"; }\n',
    )
    open_nova(server, main_uri, "fn caller() { target() }\n")
    assert hover(server, main_uri, 2)["result"]["contents"]["value"] == (
        "fn target() -> String"
    )

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": library_uri}}))
    open_nova(server, "file:///workspace/a.nova", "fn target() { return 1; }\n")
    open_nova(server, "file:///workspace/b.nova", "fn target() { return false; }\n")
    assert hover(server, main_uri, 3)["result"] is None


def test_inferred_return_hover_tracks_change_close_and_reopen() -> None:
    server = initialized_server()
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, library_uri, "fn target() { return 1; }\n")
    open_nova(server, main_uri, "fn caller() { target() }\n")
    assert hover(server, main_uri, 2)["result"]["contents"]["value"] == (
        "fn target() -> Int"
    )

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library_uri, "version": 2},
                "contentChanges": [{"text": 'fn target() { return "x"; }\n'}],
            },
        )
    )
    assert hover(server, main_uri, 3)["result"]["contents"]["value"] == (
        "fn target() -> String"
    )

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": library_uri}}))
    assert hover(server, main_uri, 4)["result"] is None

    open_nova(server, library_uri, "fn target() { return true; }\n")
    assert hover(server, main_uri, 5)["result"]["contents"]["value"] == (
        "fn target() -> Bool"
    )


def test_same_version_workspace_replacement_suppresses_inferred_hover(monkeypatch) -> None:
    server = initialized_server()
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, library_uri, "fn target() { return 1; }\n")
    open_nova(server, main_uri, "fn caller() { target() }\n")
    original = server.workspace_symbols.get(library_uri)
    assert original is not None
    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(library_uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        replace_then_commit,
    )
    assert hover(server, main_uri, 41) == {
        "jsonrpc": "2.0",
        "id": 41,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_inferred_return_hover_honors_cancellation_checkpoint(monkeypatch) -> None:
    server = initialized_server()
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/library.nova", "fn target() { return 1; }\n")
    open_nova(server, main_uri, "fn caller() { target() }\n")

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

    monkeypatch.setattr(server.requests, "checkpoint", blocked_checkpoint)
    thread = Thread(target=lambda: responses.append(hover(server, main_uri, 42)))
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
