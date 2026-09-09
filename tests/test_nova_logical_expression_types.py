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


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return {item.code for item in snapshot.diagnostics}


def hover_value(server: NovaProductLanguageServer, uri: str, text: str, request_id: int = 2) -> str:
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


def test_logical_expressions_reach_argument_and_return_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn sink(value: Int) {}\n"
        "fn main(input: Int) -> Int { sink(input < 2 && true); return !(input == 1); }\n",
    )

    codes = diagnostic_codes(server, uri)
    assert "nova.argument-type" in codes
    assert "nova.return-type" in codes


def test_logical_precedence_composes_comparison_operands() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn sink(value: Int) {}\n"
        "fn main(input: Int) { sink(input < 2 || input == 4 && true); }\n",
    )

    assert "nova.argument-type" in diagnostic_codes(server, uri)


def test_mixed_or_malformed_logical_expressions_remain_conservative() -> None:
    for index, expression in enumerate(("true && 1", "1 || false", "true &&")):
        server = initialized_server()
        uri = f"file:///workspace/conservative-{index}.nova"
        open_nova(
            server,
            uri,
            f"fn sink(value: Int) {{}}\nfn main() {{ sink({expression}); }}\n",
        )
        assert "nova.argument-type" not in diagnostic_codes(server, uri), expression


def test_logical_expression_infers_unannotated_function_result() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn source(flag: Bool) { return flag && !false; }\n"
        "fn sink(value: Int) {}\n"
        "fn main() { sink(source(true)); }\n",
    )

    assert "nova.argument-type" in diagnostic_codes(server, uri)


def test_logical_initializer_exposes_local_type_in_hover() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = input < 2 && true; value }\n"
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value: Bool"


def test_cross_file_logical_type_rebinds_on_change_close_and_reopen() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper = "fn source() -> Bool { return true; }\n"
    main = "fn sink(value: Int) {}\nfn main() { sink(source() && true); }\n"
    open_nova(server, helper_uri, helper)
    open_nova(server, main_uri, main)
    assert "nova.argument-type" in diagnostic_codes(server, main_uri)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": "fn source() -> Int { return 1; }\n"}],
            },
        )
    )
    assert "nova.argument-type" not in diagnostic_codes(server, main_uri)

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}}))
    assert "nova.argument-type" not in diagnostic_codes(server, main_uri)

    open_nova(server, helper_uri, helper, version=3)
    assert "nova.argument-type" in diagnostic_codes(server, main_uri)
