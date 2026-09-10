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


def declaration_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        item
        for item in snapshot.diagnostics
        if item.code in {"nova.uninitialized-let", "nova.untyped-var"}
    ]


def test_typed_uninitialized_var_is_accepted() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { var value: Int; value = 1; }\n")
    assert declaration_diagnostics(server, uri) == []


def test_uninitialized_let_and_untyped_var_are_rejected_with_exact_spans() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let fixed: Int; var missing; }\n"
    open_nova(server, uri, text)

    diagnostics = declaration_diagnostics(server, uri)
    assert [item.code for item in diagnostics] == [
        "nova.uninitialized-let",
        "nova.untyped-var",
    ]
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "let"
    assert text[diagnostics[1].span.start : diagnostics[1].span.end] == "missing"


def test_initialized_let_and_var_do_not_trigger_declaration_shape_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { let fixed = 1; var mutable = 1; }\n")
    assert declaration_diagnostics(server, uri) == []


def test_comments_and_strings_do_not_create_local_declaration_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn main() { // let fake: Int;\n let text = "var fake;"; /* var hidden; */ }\n',
    )
    assert declaration_diagnostics(server, uri) == []


def test_local_declaration_diagnostics_track_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = "fn main() { var value; }\n"
    valid = "fn main() { var value: Int; value = 1; }\n"
    open_nova(server, uri, invalid, 1)
    assert len(declaration_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": valid}],
            },
        )
    )
    assert declaration_diagnostics(server, uri) == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None
    open_nova(server, uri, invalid, 3)
    assert len(declaration_diagnostics(server, uri)) == 1


def test_typed_uninitialized_let_quick_fix_changes_only_keyword() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value: Int; }\n"
    open_nova(server, uri, text)
    diagnostic = declaration_diagnostics(server, uri)[0]

    response = server.handle(
        request(
            "textDocument/codeAction",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": diagnostic.span.start},
                    "end": {"line": 0, "character": diagnostic.span.end},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert response is not None
    actions = [
        action
        for action in response["result"]
        if action.get("title") == "Change uninitialized let declaration to var"
    ]
    assert len(actions) == 1
    let_start = text.index("let")
    assert actions[0]["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": let_start},
                "end": {"line": 0, "character": let_start + 3},
            },
            "newText": "var",
        }
    ]


def test_uninitialized_let_quick_fix_rejects_superseded_same_version_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value: Int; }\n"
    open_nova(server, uri, text, 1)

    first_snapshot = server.diagnostics.get(uri)
    document = server.documents.get(uri)
    assert first_snapshot is not None
    assert document is not None
    stale = tuple(
        item for item in first_snapshot.diagnostics if item.code == "nova.uninitialized-let"
    )
    assert len(stale) == 1

    semantic = server.nova_adapter.publish(server, document)
    current_snapshot = server.diagnostics.get(uri)
    assert current_snapshot is not None
    assert current_snapshot.semantic is semantic
    assert current_snapshot is not first_snapshot

    diagnostic = stale[0]
    actions = server._nova_code_actions(
        uri,
        document,
        SourceText(document.text),
        stale,
        diagnostic.span.start,
        diagnostic.span.end,
    )
    assert [
        action
        for action in actions
        if action.get("title") == "Change uninitialized let declaration to var"
    ] == []
