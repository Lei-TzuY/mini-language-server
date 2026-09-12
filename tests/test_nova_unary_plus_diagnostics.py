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


def unary_plus_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == "nova.unary-plus"]


def test_unary_plus_is_reported_in_expression_positions() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn sink(value: Int) {}\n"
        "fn main(input: Int) -> Int {\n"
        "  sink(+input);\n"
        "  let value: Int = +input;\n"
        "  return +value;\n"
        "}\n"
    )
    open_nova(server, uri, text)

    diagnostics = unary_plus_diagnostics(server, uri)
    assert len(diagnostics) == 3
    assert all(item.message == "unary '+' is not supported in Nova" for item in diagnostics)
    assert [text[item.span.start : item.span.end] for item in diagnostics] == ["+", "+", "+"]


def test_binary_plus_comments_and_strings_do_not_report_unary_plus() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn main(a: Int, b: Int) {\n'
        '  let value: Int = a + b;\n'
        '  let text: String = "ignore +value";\n'
        '  // +value\n'
        '}\n',
    )

    assert unary_plus_diagnostics(server, uri) == []


def test_nested_unary_plus_after_operator_is_reported() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main(a: Int, b: Int) { let value = a * +b; }\n")

    diagnostics = unary_plus_diagnostics(server, uri)
    assert len(diagnostics) == 1


def test_diagnostic_rebinds_after_versioned_edit() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main(input: Int) { let value = +input; }\n")
    assert len(unary_plus_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {"text": "fn main(input: Int) { let value = input + 1; }\n"}
                ],
            },
        )
    )

    assert unary_plus_diagnostics(server, uri) == []
