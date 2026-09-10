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


def assignment_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        item for item in snapshot.diagnostics if item.code == "nova.assignment-type"
    ]


def test_typed_local_and_parameter_assignments_validate_exact_rhs_spans() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn main(input: Int) { let name: String = "ok"; '
        'input = "bad"; name = false; }\n'
    )
    open_nova(server, uri, text)

    diagnostics = assignment_diagnostics(server, uri)
    assert [item.message for item in diagnostics] == [
        "assignment type mismatch: expected 'Int', got 'String'",
        "assignment type mismatch: expected 'String', got 'Bool'",
    ]
    assert [text[item.span.start : item.span.end] for item in diagnostics] == [
        '"bad"',
        "false",
    ]


def test_inferred_local_assignment_preserves_bounded_initializer_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn main() { let count = 1 + 2; count = "bad"; }\n'
    open_nova(server, uri, text)

    diagnostics = assignment_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == (
        "assignment type mismatch: expected 'Int', got 'String'"
    )
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == '"bad"'


def test_inferred_local_assignment_supports_literal_and_call_initializers() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "x"; }\n')
    text = (
        "fn main() { let flag = true; let name = helper(); "
        'flag = 1; name = false; }\n'
    )
    open_nova(server, main_uri, text)

    diagnostics = assignment_diagnostics(server, main_uri)
    assert [item.message for item in diagnostics] == [
        "assignment type mismatch: expected 'Bool', got 'Int'",
        "assignment type mismatch: expected 'String', got 'Bool'",
    ]


def test_matching_unknown_and_untyped_assignments_remain_clean() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            'fn main(input: Int, opaque) { let count: Int = 1; let value = 1; '
            "count = input + 1; count = opaque; value = true; }\n"
        ),
    )
    assert assignment_diagnostics(server, uri) == []


def test_unknown_inferred_local_type_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main(opaque) { let value = opaque; value = true; }\n",
    )
    assert assignment_diagnostics(server, uri) == []


def test_cross_file_function_result_participates_in_assignment_validation() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "x"; }\n')
    text = "fn main() { let count: Int = 1; count = helper(); }\n"
    open_nova(server, main_uri, text)

    diagnostics = assignment_diagnostics(server, main_uri)
    assert len(diagnostics) == 1
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "helper()"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": "fn helper() -> Int { return 1; }\n"}],
            },
        )
    )
    assert assignment_diagnostics(server, main_uri) == []


def test_inferred_local_assignment_revalidates_cross_file_initializer_type() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn helper() -> Int { return 1; }\n")
    text = 'fn main() { let value = helper(); value = "bad"; }\n'
    open_nova(server, main_uri, text)

    diagnostics = assignment_diagnostics(server, main_uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == (
        "assignment type mismatch: expected 'Int', got 'String'"
    )

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [
                    {"text": 'fn helper() -> String { return "x"; }\n'}
                ],
            },
        )
    )
    assert assignment_diagnostics(server, main_uri) == []


def test_assignment_diagnostics_track_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = 'fn main() { let count: Int = 1; count = "bad"; }\n'
    valid = "fn main() { let count: Int = 1; count = 2; }\n"
    open_nova(server, uri, invalid, 1)
    assert len(assignment_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": valid}],
            },
        )
    )
    assert assignment_diagnostics(server, uri) == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None
    open_nova(server, uri, invalid, 3)
    assert len(assignment_diagnostics(server, uri)) == 1


def test_inferred_assignment_diagnostics_track_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    int_source = 'fn main() { let value = 1 + 2; value = "bad"; }\n'
    string_source = 'fn main() { let value = "ok"; value = "good"; }\n'
    open_nova(server, uri, int_source, 1)
    assert len(assignment_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": string_source}],
            },
        )
    )
    assert assignment_diagnostics(server, uri) == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None
    open_nova(server, uri, int_source, 3)
    assert len(assignment_diagnostics(server, uri)) == 1


def test_assignment_quick_fix_replaces_only_diagnosed_rhs() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn main() { let count: Int = 1; count = "bad"; }\n'
    open_nova(server, uri, text)
    start = text.index('"bad"')

    response = server.handle(
        request(
            "textDocument/codeAction",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": start},
                    "end": {"line": 0, "character": start + 5},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert response is not None
    actions = [
        action
        for action in response["result"]
        if action.get("title") == "Replace assignment value with Int literal"
    ]
    assert len(actions) == 1
    assert actions[0]["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": start},
                "end": {"line": 0, "character": start + 5},
            },
            "newText": "0",
        }
    ]


def test_inferred_assignment_quick_fix_uses_inferred_target_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn main() { let count = 1 + 2; count = "bad"; }\n'
    open_nova(server, uri, text)
    start = text.index('"bad"')

    response = server.handle(
        request(
            "textDocument/codeAction",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": start},
                    "end": {"line": 0, "character": start + 5},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert response is not None
    actions = [
        action
        for action in response["result"]
        if action.get("title") == "Replace assignment value with Int literal"
    ]
    assert len(actions) == 1
    assert actions[0]["edit"]["changes"][uri][0]["newText"] == "0"


def test_assignment_quick_fix_rejects_superseded_same_version_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn main() { let count: Int = 1; count = "bad"; }\n'
    open_nova(server, uri, text, 1)

    first_snapshot = server.diagnostics.get(uri)
    document = server.documents.get(uri)
    assert first_snapshot is not None
    assert document is not None
    stale = tuple(
        item
        for item in first_snapshot.diagnostics
        if item.code == "nova.assignment-type"
    )
    assert len(stale) == 1

    semantic = server.nova_adapter.publish(server, document)
    current_snapshot = server.diagnostics.get(uri)
    assert current_snapshot is not None
    assert current_snapshot.semantic is semantic
    assert current_snapshot is not first_snapshot

    start = text.index('"bad"')
    actions = server._nova_code_actions(
        uri,
        document,
        SourceText(document.text),
        stale,
        start,
        start + 5,
    )
    assert [
        action
        for action in actions
        if action.get("title") == "Replace assignment value with Int literal"
    ] == []
