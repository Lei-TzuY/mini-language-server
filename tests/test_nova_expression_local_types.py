from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server(*, inlay_hints: bool = False) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    capabilities = {"textDocument": {"inlayHint": {}}} if inlay_hints else {}
    result = server.handle(request("initialize", 1, {"capabilities": capabilities}))
    assert result is not None
    return server


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
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


def hover_value(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    request_id: int = 2,
) -> str:
    offset = text.rindex("value")
    response = server.handle(
        request(
            "textDocument/hover",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": offset},
            },
        )
    )
    assert response is not None
    return response["result"]["contents"]["value"]


def completion_detail(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    request_id: int = 3,
) -> str:
    offset = text.rindex("value")
    response = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": offset},
            },
        )
    )
    assert response is not None
    item = next(item for item in response["result"] if item["label"] == "value")
    return item["detail"]


def local_type_hint_labels(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    request_id: int = 4,
) -> list[str]:
    response = server.handle(
        request(
            "textDocument/inlayHint",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": len(text.rstrip("\n"))},
                },
            },
        )
    )
    assert response is not None
    return [item["label"] for item in response["result"] if item.get("kind") == 1]


def test_comparison_initializer_exposes_local_type_in_hover() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = input + 2 >= 3; value }\n"
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value: Bool"


def test_comparison_initializer_exposes_local_type_in_completion_detail() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = input >= 1; value }\n"
    open_nova(server, uri, text)

    assert completion_detail(server, uri, text) == "variable: Bool"


def test_comparison_initializer_exposes_local_type_in_inlay_hints() -> None:
    server = initialized_server(inlay_hints=True)
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = (input + 1) == 2; value }\n"
    open_nova(server, uri, text)

    assert local_type_hint_labels(server, uri, text) == [": Bool"]


def test_mixed_comparison_initializer_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = input == true; value }\n"
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value"


def test_cross_file_comparison_local_type_rebinds_after_change() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper = "fn source() -> Int { return 1; }\n"
    main = "fn main() { let value = source() == 1; value }\n"
    open_nova(server, helper_uri, helper)
    open_nova(server, main_uri, main)

    assert hover_value(server, main_uri, main) == "variable value: Bool"

    changed = "fn source() -> Bool { return true; }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": changed}],
            },
        )
    )
    assert hover_value(server, main_uri, main, request_id=5) == "variable value"


def test_arithmetic_initializer_exposes_local_type_in_hover() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = (input + 2) * 3; value }\n"
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value: Int"


def test_arithmetic_initializer_exposes_local_type_in_completion_detail() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = input + 2 * 3; value }\n"
    open_nova(server, uri, text)

    assert completion_detail(server, uri, text) == "variable: Int"


def test_arithmetic_initializer_exposes_local_type_in_inlay_hints() -> None:
    server = initialized_server(inlay_hints=True)
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = (input + 1) * 2; value }\n"
    open_nova(server, uri, text)

    assert local_type_hint_labels(server, uri, text) == [": Int"]


def test_mixed_arithmetic_initializer_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = input + true; value }\n"
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value"


def test_cross_file_arithmetic_local_type_rebinds_after_change() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper = "fn source() -> Int { return 1; }\n"
    main = "fn main() { let value = source() + 1; value }\n"
    open_nova(server, helper_uri, helper)
    open_nova(server, main_uri, main)

    assert hover_value(server, main_uri, main) == "variable value: Int"

    changed = "fn source() -> Bool { return true; }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": changed}],
            },
        )
    )
    assert hover_value(server, main_uri, main, request_id=6) == "variable value"


def test_arithmetic_local_type_rebinds_after_close_and_reopen() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper = "fn source() -> Int { return 1; }\n"
    main = "fn main() { let value = source() + 1; value }\n"
    open_nova(server, helper_uri, helper)
    open_nova(server, main_uri, main)

    assert hover_value(server, main_uri, main) == "variable value: Int"

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}})
    )
    assert hover_value(server, main_uri, main, request_id=7) == "variable value"

    open_nova(
        server,
        helper_uri,
        "fn source() -> Int { return 2; }\n",
        version=1,
    )
    assert hover_value(server, main_uri, main, request_id=8) == "variable value: Int"
