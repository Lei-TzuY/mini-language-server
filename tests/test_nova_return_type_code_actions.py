from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"codeAction": {}}}},
        )
    )
    assert result is not None
    assert result["result"]["capabilities"]["codeActionProvider"] is True
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


def code_action(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    start: int,
    end: int,
) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": start},
                    "end": {"line": 0, "character": end},
                },
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert result is not None
    return result


def test_return_type_mismatches_get_deterministic_default_literals() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn as_int() -> Int { return "x" } '
        "fn as_string() -> String { return false } "
        "fn as_bool() -> Bool { return 1 }\n"
    )
    open_nova(server, uri, text)

    for request_id, (expression, expected, replacement) in enumerate(
        [('"x"', "Int", "0"), ("false", "String", '""'), ("1", "Bool", "false")],
        start=2,
    ):
        start = text.index(expression)
        result = code_action(server, uri, request_id, start, start + len(expression))
        actions = result["result"]
        assert len(actions) == 1
        action = actions[0]
        assert expected in action["title"]
        edit = action["edit"]["changes"][uri][0]
        assert edit["newText"] == replacement
        assert edit["range"]["start"] == {"line": 0, "character": start}
        assert edit["range"]["end"] == {
            "line": 0,
            "character": start + len(expression),
        }


def test_reference_return_mismatch_gets_repair_from_current_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn target(value: String) -> Int { return value }\n"
    open_nova(server, uri, text)
    start = text.index("value", text.index("return"))

    result = code_action(server, uri, 2, start, start + len("value"))
    actions = result["result"]
    assert len(actions) == 1
    assert actions[0]["edit"]["changes"][uri][0]["newText"] == "0"

    changed = "fn target(value: Int) -> Int { return value }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": changed}],
            },
        )
    )
    changed_start = changed.index("value", changed.index("return"))
    assert code_action(
        server, uri, 3, changed_start, changed_start + len("value")
    )["result"] == []
