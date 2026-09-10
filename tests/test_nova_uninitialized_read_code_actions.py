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


def diagnostic(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    matches = [item for item in snapshot.diagnostics if item.code == "nova.uninitialized-read"]
    assert len(matches) == 1
    return matches[0]


def quick_fixes(server: NovaProductLanguageServer, uri: str, item) -> list[dict]:
    response = server.handle(
        request(
            "textDocument/codeAction",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": item.span.start},
                    "end": {"line": 0, "character": item.span.end},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert response is not None
    return [
        action
        for action in response["result"]
        if action.get("title", "").startswith("Initialize '")
    ]


def test_uninitialized_read_quick_fix_initializes_exact_int_declaration() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { var value: Int; let copy = value; }\n"
    open_nova(server, uri, text)
    item = diagnostic(server, uri)

    actions = quick_fixes(server, uri, item)
    assert len(actions) == 1
    semicolon = text.index(";", text.index("var value"))
    assert actions[0]["title"] == "Initialize 'value' at declaration"
    assert actions[0]["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": semicolon},
                "end": {"line": 0, "character": semicolon},
            },
            "newText": " = 0",
        }
    ]


def test_uninitialized_read_quick_fix_uses_bounded_type_defaults() -> None:
    cases = [("String", ' = ""'), ("Bool", " = false")]
    for index, (type_name, expected) in enumerate(cases, start=1):
        server = initialized_server()
        uri = f"file:///workspace/{index}.nova"
        text = f"fn main() {{ var value: {type_name}; let copy = value; }}\n"
        open_nova(server, uri, text)
        item = diagnostic(server, uri)

        actions = quick_fixes(server, uri, item)
        assert len(actions) == 1
        assert actions[0]["edit"]["changes"][uri][0]["newText"] == expected


def test_uninitialized_read_quick_fix_does_not_guess_unsupported_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { var value: Widget; let copy = value; }\n"
    open_nova(server, uri, text)
    item = diagnostic(server, uri)
    assert quick_fixes(server, uri, item) == []


def test_uninitialized_read_quick_fix_rejects_superseded_same_version_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { var value: Int; let copy = value; }\n"
    open_nova(server, uri, text, 1)

    first_snapshot = server.diagnostics.get(uri)
    document = server.documents.get(uri)
    assert first_snapshot is not None
    assert document is not None
    stale = tuple(
        item for item in first_snapshot.diagnostics if item.code == "nova.uninitialized-read"
    )
    assert len(stale) == 1

    semantic = server.nova_adapter.publish(server, document)
    current_snapshot = server.diagnostics.get(uri)
    assert current_snapshot is not None
    assert current_snapshot.semantic is semantic
    assert current_snapshot is not first_snapshot

    item = stale[0]
    actions = server._nova_code_actions(
        uri,
        document,
        SourceText(document.text),
        stale,
        item.span.start,
        item.span.end,
    )
    assert [
        action
        for action in actions
        if action.get("title") == "Initialize 'value' at declaration"
    ] == []


def test_uninitialized_read_quick_fix_tracks_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = "fn main() { var value: Int; let copy = value; }\n"
    valid = "fn main() { var value: Int; value = 1; let copy = value; }\n"
    open_nova(server, uri, invalid, 1)
    assert len(quick_fixes(server, uri, diagnostic(server, uri))) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": valid}],
            },
        )
    )
    current = server.diagnostics.get(uri)
    assert current is not None
    assert [item for item in current.diagnostics if item.code == "nova.uninitialized-read"] == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, invalid, 3)
    assert len(quick_fixes(server, uri, diagnostic(server, uri))) == 1
