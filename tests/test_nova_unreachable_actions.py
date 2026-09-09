from __future__ import annotations

from mini_language_server import NovaProductLanguageServer, SourceText


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    return server


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
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


def remove_actions(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    line: int,
    start: int,
    end: int,
    request_id: int,
) -> list[dict]:
    response = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": line, "character": start},
                    "end": {"line": line, "character": end},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert response is not None
    return [
        action
        for action in response["result"]
        if action.get("title") == "Remove unreachable code"
    ]


def test_unreachable_quick_fix_deletes_exact_diagnostic_suffix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { return; let value = 1; let other = 2; }\n"
    open_nova(server, uri, text)

    suffix = "let value = 1; let other = 2;"
    start = text.index(suffix)
    actions = remove_actions(
        server,
        uri,
        line=0,
        start=start,
        end=start + len(suffix),
        request_id=2,
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["kind"] == "quickfix"
    assert action["diagnostics"][0]["code"] == "nova.unreachable-code"
    assert action["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": start},
                "end": {"line": 0, "character": start + len(suffix)},
            },
            "newText": "",
        }
    ]


def test_unreachable_quick_fix_tracks_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    unreachable_text = "fn main() { return; let value = 1; }\n"
    reachable_text = "fn main() { let value = 1; }\n"
    suffix = "let value = 1;"
    start = unreachable_text.index(suffix)

    open_nova(server, uri, unreachable_text, version=1)
    assert remove_actions(
        server,
        uri,
        line=0,
        start=start,
        end=start + len(suffix),
        request_id=2,
    )

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": reachable_text}],
            },
        )
    )
    reachable_start = reachable_text.index(suffix)
    assert remove_actions(
        server,
        uri,
        line=0,
        start=reachable_start,
        end=reachable_start + len(suffix),
        request_id=3,
    ) == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.documents.get(uri) is None
    assert server.diagnostics.get(uri) is None

    open_nova(server, uri, unreachable_text, version=3)
    assert remove_actions(
        server,
        uri,
        line=0,
        start=start,
        end=start + len(suffix),
        request_id=4,
    )


def test_unreachable_quick_fix_rejects_superseded_same_version_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { return; let value = 1; }\n"
    suffix = "let value = 1;"
    start = text.index(suffix)
    open_nova(server, uri, text, version=1)

    first_snapshot = server.diagnostics.get(uri)
    document = server.documents.get(uri)
    assert first_snapshot is not None
    assert document is not None
    stale = tuple(
        diagnostic
        for diagnostic in first_snapshot.diagnostics
        if diagnostic.code == "nova.unreachable-code"
    )
    assert len(stale) == 1

    semantic = server.nova_adapter.publish(server, document)
    current_snapshot = server.diagnostics.get(uri)
    assert current_snapshot is not None
    assert current_snapshot.semantic is semantic
    assert current_snapshot is not first_snapshot

    actions = server._nova_code_actions(
        uri,
        document,
        SourceText(document.text),
        stale,
        start,
        start + len(suffix),
    )
    assert [
        action
        for action in actions
        if action.get("title") == "Remove unreachable code"
    ] == []
