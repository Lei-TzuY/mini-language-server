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


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> list[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item.code for item in snapshot.diagnostics]


def test_unary_plus_reaches_argument_return_and_local_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn take_bool(value: Bool) {}\n"
        "fn inferred() { +1 }\n"
        "fn main() -> Bool {\n"
        "  let local: Bool = +1;\n"
        "  take_bool(inferred());\n"
        "  return +(1 + 2);\n"
        "}\n",
    )

    codes = diagnostic_codes(server, uri)
    assert "nova.local-type" in codes
    assert "nova.argument-type" in codes
    assert "nova.return-type" in codes


def test_unary_plus_preserves_int_and_uint_references() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn take_int(value: Int) {}\n"
        "fn take_uint(value: UInt) {}\n"
        "fn main(i: Int, u: UInt) {\n"
        "  let int_value: Int = +i;\n"
        "  let uint_value: UInt = +u;\n"
        "  take_int(+int_value);\n"
        "  take_uint(+(uint_value));\n"
        "}\n",
    )

    codes = diagnostic_codes(server, uri)
    assert "nova.local-type" not in codes
    assert "nova.argument-type" not in codes


def test_unary_plus_non_numeric_operands_remain_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn take_bool(value: Bool) {}\nfn main() { take_bool(+"value"); }\n',
    )

    assert "nova.argument-type" not in diagnostic_codes(server, uri)


def test_cross_file_unary_plus_rebinds_after_result_type_change() -> None:
    server = initialized_server()
    source_uri = "file:///workspace/source.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, source_uri, "fn source() -> Int { return 1; }\n")
    open_nova(
        server,
        main_uri,
        "fn take_bool(value: Bool) {}\nfn main() { take_bool(+source()); }\n",
    )
    assert "nova.argument-type" in diagnostic_codes(server, main_uri)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": source_uri, "version": 2},
                "contentChanges": [
                    {"text": 'fn source() -> String { return "value"; }\n'}
                ],
            },
        )
    )

    assert "nova.argument-type" not in diagnostic_codes(server, main_uri)
