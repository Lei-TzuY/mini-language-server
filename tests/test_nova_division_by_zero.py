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


def zero_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        item
        for item in snapshot.diagnostics
        if item.code == "nova.division-by-zero"
    ]


def replacement_actions(
    server: NovaProductLanguageServer,
    uri: str,
    start: int,
    end: int,
    request_id: int,
):
    response = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": start},
                    "end": {"line": 0, "character": end},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert response is not None
    return [
        action
        for action in response["result"]
        if action.get("title") == "Replace zero divisor with 1"
    ]


def test_literal_zero_divisors_report_exact_spans_and_ignore_trivia() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn main() { let a = 10 / 0; let b = 11 % -0; '
        'let s = "12 / 0"; /* 13 % 0 */ }\n'
    )
    open_nova(server, uri, text)

    diagnostics = zero_diagnostics(server, uri)
    assert len(diagnostics) == 2
    assert [
        text[item.span.start : item.span.end] for item in diagnostics
    ] == ["0", "-0"]


def test_zero_divisor_quick_fix_replaces_only_diagnosed_literal() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 10 / 0; }\n"
    open_nova(server, uri, text)
    start = text.index("0", text.index("/"))

    actions = replacement_actions(server, uri, start, start + 1, 2)
    assert len(actions) == 1
    assert actions[0]["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": start},
                "end": {"line": 0, "character": start + 1},
            },
            "newText": "1",
        }
    ]


def test_zero_divisor_diagnostics_track_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = "fn main() { let value = 10 / 0; }\n"
    valid = "fn main() { let value = 10 / 2; }\n"
    open_nova(server, uri, invalid, 1)
    assert len(zero_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": valid}],
            },
        )
    )
    assert zero_diagnostics(server, uri) == []

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": uri}})
    )
    assert server.diagnostics.get(uri) is None
    open_nova(server, uri, invalid, 3)
    assert len(zero_diagnostics(server, uri)) == 1


def test_zero_divisor_quick_fix_rejects_superseded_same_version_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 10 / 0; }\n"
    open_nova(server, uri, text, 1)

    first_snapshot = server.diagnostics.get(uri)
    document = server.documents.get(uri)
    assert first_snapshot is not None
    assert document is not None
    stale = tuple(
        item
        for item in first_snapshot.diagnostics
        if item.code == "nova.division-by-zero"
    )
    assert len(stale) == 1

    semantic = server.nova_adapter.publish(server, document)
    current_snapshot = server.diagnostics.get(uri)
    assert current_snapshot is not None
    assert current_snapshot.semantic is semantic
    assert current_snapshot is not first_snapshot

    start = text.index("0", text.index("/"))
    actions = server._nova_code_actions(
        uri,
        document,
        SourceText(document.text),
        stale,
        start,
        start + 1,
    )
    assert [
        action
        for action in actions
        if action.get("title") == "Replace zero divisor with 1"
    ] == []
