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


def test_constant_expression_zero_divisors_report_exact_repair_spans() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() { let a = 10 / (1 - 1); let b = 11 % (2 * 3 - 6); "
        "let c = 12 / ((7 % 7)); }\n"
    )
    open_nova(server, uri, text)

    diagnostics = zero_diagnostics(server, uri)
    assert len(diagnostics) == 3
    assert [
        text[item.span.start : item.span.end] for item in diagnostics
    ] == ["1 - 1", "2 * 3 - 6", "7 % 7"]


def test_constant_expression_evaluation_obeys_precedence_and_truncation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() { let a = 10 / (8 - 2 * 4); "
        "let b = 11 % (-3 / 2 + 1); let c = 12 / (9 % 4 - 1); }\n"
    )
    open_nova(server, uri, text)

    diagnostics = zero_diagnostics(server, uri)
    assert len(diagnostics) == 3
    assert [
        text[item.span.start : item.span.end] for item in diagnostics
    ] == ["8 - 2 * 4", "-3 / 2 + 1", "9 % 4 - 1"]


def test_unknown_nonzero_and_invalid_constant_expressions_remain_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() { let x = 1; let a = 10 / (x - x); let b = 11 % (2 + 1); "
        "let c = 12 / (1 + ); }\n"
    )
    open_nova(server, uri, text)

    assert zero_diagnostics(server, uri) == []


def test_nested_invalid_constant_division_reports_only_inner_zero() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 10 / (4 / (2 - 2)); }\n"
    open_nova(server, uri, text)

    diagnostics = zero_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "2 - 2"


def test_constant_expression_quick_fix_preserves_outer_parentheses() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 10 / ((3 - 3)); }\n"
    open_nova(server, uri, text)
    start = text.index("(3 - 3)")
    end = start + len("(3 - 3)")

    actions = replacement_actions(server, uri, start, end, 2)
    assert len(actions) == 1
    assert actions[0]["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": start},
                "end": {"line": 0, "character": end},
            },
            "newText": "1",
        }
    ]


def test_constant_zero_diagnostics_track_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = "fn main() { let value = 10 / (4 - 4); }\n"
    valid = "fn main() { let value = 10 / (4 - 3); }\n"
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


def test_constant_zero_quick_fix_rejects_same_version_stale_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 10 / ((5 - 5)); }\n"
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

    start = text.index("(5 - 5)")
    end = start + len("(5 - 5)")
    actions = server._nova_code_actions(
        uri,
        document,
        SourceText(document.text),
        stale,
        start,
        end,
    )
    assert [
        action
        for action in actions
        if action.get("title") == "Replace zero divisor with 1"
    ] == []
