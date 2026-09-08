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
            2,
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": offset}},
        )
    )
    assert response is not None
    return response["result"]["contents"]["value"]


def test_same_file_call_initializer_exposes_local_type_in_hover() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn helper() -> String { return \"x\"; } fn main() { let value = helper() value }\n"
    open_nova(server, uri, text)
    assert hover_value(server, uri, text) == "variable value: String"


def test_ambiguous_same_file_call_initializer_remains_untyped() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn helper() -> Int { return 1; } fn helper() -> String { return \"x\"; } "
        "fn main() { let value = helper() value }\n"
    )
    open_nova(server, uri, text)
    assert hover_value(server, uri, text) == "variable value"


def test_non_call_initializer_expression_is_not_misclassified() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn helper() -> Int { return 1; } fn main() { let value = helper() + 1 value }\n"
    open_nova(server, uri, text)
    assert hover_value(server, uri, text) == "variable value"


def test_call_initializer_type_recomputes_after_change_and_close_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    first = "fn helper() -> Int { return 1; } fn main() { let value = helper() value }\n"
    open_nova(server, uri, first)
    assert hover_value(server, uri, first) == "variable value: Int"

    second = "fn helper() -> Bool { return true; } fn main() { let value = helper() value }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": second}]},
        )
    )
    assert hover_value(server, uri, second) == "variable value: Bool"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    third = "fn helper() -> String { return \"x\"; } fn main() { let value = helper() value }\n"
    open_nova(server, uri, third)
    assert hover_value(server, uri, third) == "variable value: String"


def test_call_initializer_type_appears_in_completion_detail() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn helper() -> Int { return 1; } fn main() { let value = helper() value }\n"
    open_nova(server, uri, text)
    offset = text.rindex("value")
    response = server.handle(
        request(
            "textDocument/completion",
            3,
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": offset}},
        )
    )
    assert response is not None
    item = next(item for item in response["result"] if item["label"] == "value")
    assert item["detail"] == "variable: Int"
