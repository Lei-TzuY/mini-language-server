from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    return server


def open_nova(
    server: NovaProductLanguageServer, uri: str, text: str, version: int = 1
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


def code_actions(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    line: int,
    start: int,
    end: int,
    request_id: int = 2,
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
    return response["result"]


def condition_actions(actions: list[dict]) -> list[dict]:
    return [
        action
        for action in actions
        if action.get("title") == "Replace condition with Bool literal"
    ]


def typed_condition_actions(actions: list[dict]) -> list[dict]:
    return [
        action for action in actions if action.get("title", "").startswith("Compare ")
    ]


def test_condition_type_quick_fix_targets_exact_trimmed_expression() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { if ( 1 + 2 ) { let value = 1; } }\n"
    open_nova(server, uri, text)

    expression = "1 + 2"
    start = text.index(expression)
    actions = condition_actions(
        code_actions(server, uri, line=0, start=start, end=start + len(expression))
    )
    assert len(actions) == 1
    action = actions[0]
    assert action["kind"] == "quickfix"
    assert action["diagnostics"][0]["code"] == "nova.condition-type"
    edit = action["edit"]["changes"][uri]
    assert edit == [
        {
            "range": {
                "start": {"line": 0, "character": start},
                "end": {"line": 0, "character": start + len(expression)},
            },
            "newText": "false",
        }
    ]


def test_int_condition_type_quick_fix_preserves_expression_via_zero_comparison() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { if ( 1 + 2 ) { let value = 1; } }\n"
    open_nova(server, uri, text)

    expression = "1 + 2"
    start = text.index(expression)
    actions = typed_condition_actions(
        code_actions(server, uri, line=0, start=start, end=start + len(expression))
    )
    assert len(actions) == 1
    action = actions[0]
    assert action["title"] == "Compare Int condition with zero"
    assert action["diagnostics"][0]["code"] == "nova.condition-type"
    assert action["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": start},
                "end": {"line": 0, "character": start + len(expression)},
            },
            "newText": "(1 + 2) != 0",
        }
    ]


def test_string_condition_type_quick_fix_preserves_expression_via_empty_comparison() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn main() { while ("value") { let value = 1; } }\n'
    open_nova(server, uri, text)

    expression = '"value"'
    start = text.index(expression)
    actions = typed_condition_actions(
        code_actions(server, uri, line=0, start=start, end=start + len(expression))
    )
    assert len(actions) == 1
    action = actions[0]
    assert action["title"] == "Compare String condition with empty string"
    assert action["edit"]["changes"][uri][0]["newText"] == '("value") != ""'


def test_condition_type_quick_fix_rebinds_across_workspace_lifecycle() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper_int = "fn source() -> Int { return 1; }\n"
    helper_bool = "fn source() -> Bool { return true; }\n"
    main = "fn main() { while (source()) { let value = 1; } }\n"
    expression = "source()"
    start = main.index(expression)

    open_nova(server, helper_uri, helper_int, version=1)
    open_nova(server, main_uri, main, version=1)
    initial_actions = code_actions(
        server, main_uri, line=0, start=start, end=start + len(expression)
    )
    assert condition_actions(initial_actions)
    typed = typed_condition_actions(initial_actions)
    assert len(typed) == 1
    assert typed[0]["edit"]["changes"][main_uri][0]["newText"] == "(source()) != 0"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": helper_bool}],
            },
        )
    )
    changed_actions = code_actions(
        server,
        main_uri,
        line=0,
        start=start,
        end=start + len(expression),
        request_id=3,
    )
    assert condition_actions(changed_actions) == []
    assert typed_condition_actions(changed_actions) == []

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}})
    )
    closed_actions = code_actions(
        server,
        main_uri,
        line=0,
        start=start,
        end=start + len(expression),
        request_id=4,
    )
    assert condition_actions(closed_actions) == []
    assert typed_condition_actions(closed_actions) == []

    open_nova(server, helper_uri, helper_int, version=3)
    reopened_actions = code_actions(
        server,
        main_uri,
        line=0,
        start=start,
        end=start + len(expression),
        request_id=5,
    )
    assert condition_actions(reopened_actions)
    typed = typed_condition_actions(reopened_actions)
    assert len(typed) == 1
    assert typed[0]["edit"]["changes"][main_uri][0]["newText"] == "(source()) != 0"
