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


def structural_actions(
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
        if action.get("title", "").startswith("Remove unreachable constant-")
    ]


def unreachable_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return tuple(
        diagnostic
        for diagnostic in snapshot.diagnostics
        if diagnostic.code == "nova.unreachable-code"
    )


def test_constant_false_while_action_removes_entire_loop() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    statement = "while (false) { let dead = 1; }"
    text = f"fn main() {{ {statement} let live = 2; }}\n"
    open_nova(server, uri, text)

    dead = "let dead = 1;"
    dead_start = text.index(dead)
    statement_start = text.index(statement)
    actions = structural_actions(
        server,
        uri,
        line=0,
        start=dead_start,
        end=dead_start + len(dead),
        request_id=2,
    )

    assert len(actions) == 1
    assert actions[0]["title"] == "Remove unreachable constant-false while"
    assert actions[0]["diagnostics"][0]["code"] == "nova.unreachable-code"
    assert actions[0]["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": statement_start},
                "end": {
                    "line": 0,
                    "character": statement_start + len(statement),
                },
            },
            "newText": "",
        }
    ]


def test_constant_false_if_without_else_action_removes_entire_if() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    statement = "if (false) { let dead = 1; }"
    text = f"fn main() {{ {statement} let live = 2; }}\n"
    open_nova(server, uri, text)

    dead = "let dead = 1;"
    dead_start = text.index(dead)
    actions = structural_actions(
        server,
        uri,
        line=0,
        start=dead_start,
        end=dead_start + len(dead),
        request_id=2,
    )

    assert len(actions) == 1
    assert actions[0]["title"] == "Remove unreachable constant-false if"


def test_constant_false_if_with_else_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { if (false) { let dead = 1; } else { let live = 2; } }\n"
    open_nova(server, uri, text)

    dead = "let dead = 1;"
    start = text.index(dead)
    assert structural_actions(
        server,
        uri,
        line=0,
        start=start,
        end=start + len(dead),
        request_id=2,
    ) == []


def test_constant_true_else_action_removes_entire_else_clause() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    else_clause = "else { let dead = 2; }"
    text = f"fn main() {{ if (true) {{ let live = 1; }} {else_clause} }}\n"
    open_nova(server, uri, text)

    dead = "let dead = 2;"
    dead_start = text.index(dead)
    else_start = text.index(else_clause)
    actions = structural_actions(
        server,
        uri,
        line=0,
        start=dead_start,
        end=dead_start + len(dead),
        request_id=2,
    )

    assert len(actions) == 1
    assert actions[0]["title"] == "Remove unreachable constant-true else"
    assert actions[0]["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": else_start},
                "end": {"line": 0, "character": else_start + len(else_clause)},
            },
            "newText": "",
        }
    ]


def test_constant_false_else_if_does_not_offer_dangling_else_repair() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(first: Bool) { if (first) { let live = 1; } "
        "else if (false) { let dead = 2; } }\n"
    )
    open_nova(server, uri, text)

    dead = "let dead = 2;"
    start = text.index(dead)
    assert structural_actions(
        server,
        uri,
        line=0,
        start=start,
        end=start + len(dead),
        request_id=2,
    ) == []


def test_structural_action_rejects_superseded_same_version_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { while (false) { let dead = 1; } }\n"
    open_nova(server, uri, text, version=1)

    document = server.documents.get(uri)
    first_snapshot = server.diagnostics.get(uri)
    assert document is not None
    assert first_snapshot is not None
    stale = unreachable_diagnostics(server, uri)
    assert len(stale) == 1

    server.nova_adapter.publish(server, document)
    current_snapshot = server.diagnostics.get(uri)
    assert current_snapshot is not None
    assert current_snapshot is not first_snapshot

    dead = "let dead = 1;"
    start = text.index(dead)
    actions = server._nova_code_actions(
        uri,
        document,
        SourceText(document.text),
        stale,
        start,
        start + len(dead),
    )
    assert [
        action
        for action in actions
        if action.get("title", "").startswith("Remove unreachable constant-")
    ] == []


def test_structural_action_tracks_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    dead_text = "fn main() { while (false) { let dead = 1; } }\n"
    live_text = "fn main(flag: Bool) { while (flag) { let live = 1; } }\n"
    dead = "let dead = 1;"
    dead_start = dead_text.index(dead)

    open_nova(server, uri, dead_text, version=1)
    assert structural_actions(
        server,
        uri,
        line=0,
        start=dead_start,
        end=dead_start + len(dead),
        request_id=2,
    )

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": live_text}],
            },
        )
    )
    live = "let live = 1;"
    live_start = live_text.index(live)
    assert structural_actions(
        server,
        uri,
        line=0,
        start=live_start,
        end=live_start + len(live),
        request_id=3,
    ) == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.documents.get(uri) is None
    assert server.diagnostics.get(uri) is None

    open_nova(server, uri, dead_text, version=3)
    assert structural_actions(
        server,
        uri,
        line=0,
        start=dead_start,
        end=dead_start + len(dead),
        request_id=4,
    )
