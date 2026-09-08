from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(
        request("initialize", 1, {"capabilities": {"textDocument": {"inlayHint": {}}}})
    ) is not None
    return server


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": "nova", "version": 1, "text": text}},
        )
    )


def hover_value(server: NovaProductLanguageServer, uri: str, text: str) -> str:
    offset = text.rindex("value")
    response = server.handle(
        request(
            "textDocument/hover",
            10,
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": offset}},
        )
    )
    assert response is not None
    return response["result"]["contents"]["value"]


def test_cross_file_call_initializer_exposes_local_type_in_hover() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper = 'fn helper() -> String { return "x"; }\n'
    main = "fn main() { let value = helper() value }\n"
    open_nova(server, helper_uri, helper)
    open_nova(server, main_uri, main)

    assert hover_value(server, main_uri, main) == "variable value: String"


def test_cross_file_call_initializer_type_appears_in_completion_and_inlay_hint() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper = "fn helper() -> Int { return 1; }\n"
    main = "fn main() { let value = helper() value }\n"
    open_nova(server, helper_uri, helper)
    open_nova(server, main_uri, main)

    offset = main.rindex("value")
    completion = server.handle(
        request(
            "textDocument/completion",
            11,
            {"textDocument": {"uri": main_uri}, "position": {"line": 0, "character": offset}},
        )
    )
    assert completion is not None
    item = next(item for item in completion["result"] if item["label"] == "value")
    assert item["detail"] == "variable: Int"

    hints = server.handle(
        request(
            "textDocument/inlayHint",
            12,
            {
                "textDocument": {"uri": main_uri},
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": len(main.rstrip("\n"))},
                },
            },
        )
    )
    assert hints is not None
    assert any(hint.get("label") == ": Int" for hint in hints["result"])


def test_ambiguous_cross_file_call_initializer_remains_untyped() -> None:
    server = initialized_server()
    main_uri = "file:///workspace/main.nova"
    main = "fn main() { let value = helper() value }\n"
    open_nova(server, "file:///workspace/a.nova", "fn helper() -> Int { return 1; }\n")
    open_nova(server, "file:///workspace/b.nova", 'fn helper() -> String { return "x"; }\n')
    open_nova(server, main_uri, main)

    assert hover_value(server, main_uri, main) == "variable value"


def test_cross_file_call_initializer_recomputes_after_helper_change() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    main = "fn main() { let value = helper() value }\n"
    open_nova(server, helper_uri, "fn helper() -> Int { return 1; }\n")
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value: Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": "fn helper() -> Bool { return true; }\n"}],
            },
        )
    )
    assert hover_value(server, main_uri, main) == "variable value: Bool"


def test_cross_file_call_initializer_rebinds_after_close_reopen() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    main = "fn main() { let value = helper() value }\n"
    open_nova(server, helper_uri, "fn helper() -> Int { return 1; }\n")
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value: Int"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}}))
    assert hover_value(server, main_uri, main) == "variable value"

    open_nova(server, helper_uri, 'fn helper() -> String { return "x"; }\n')
    assert hover_value(server, main_uri, main) == "variable value: String"
