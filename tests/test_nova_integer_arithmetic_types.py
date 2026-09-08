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


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return {item.code for item in snapshot.diagnostics}


def test_integer_arithmetic_reaches_argument_and_return_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn sink(value: Bool) {}\n"
        "fn main() -> Bool { sink(1 + 2 * 3); return (4 + 5); }\n",
    )

    codes = diagnostic_codes(server, uri)
    assert "nova.argument-type" in codes
    assert "nova.return-type" in codes


def test_integer_arithmetic_uses_exact_typed_reference_operands() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main(input: Int) -> Bool { return input + 1; }\n",
    )

    assert "nova.return-type" in diagnostic_codes(server, uri)


def test_unsupported_mixed_arithmetic_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn sink(value: Bool) {}\nfn main() { sink("value" + 1) }\n',
    )

    assert "nova.argument-type" not in diagnostic_codes(server, uri)


def test_cross_file_arithmetic_rebinds_after_result_type_change() -> None:
    server = initialized_server()
    source_uri = "file:///workspace/source.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, source_uri, "fn source() -> Int { return 1; }\n")
    open_nova(
        server,
        main_uri,
        "fn sink(value: Bool) {}\nfn main() { sink(source() + 1) }\n",
    )
    assert "nova.argument-type" in diagnostic_codes(server, main_uri)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": source_uri, "version": 2},
                "contentChanges": [
                    {"text": "fn source() -> Bool { return true; }\n"}
                ],
            },
        )
    )

    assert "nova.argument-type" not in diagnostic_codes(server, main_uri)


def test_arithmetic_argument_mismatch_exposes_existing_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn sink(value: Bool) {}\nfn main() { sink(1 + 2) }\n"
    open_nova(server, uri, text)

    line = text.splitlines()[1]
    start = line.index("1 + 2")
    result = server.handle(
        request(
            "textDocument/codeAction",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 1, "character": start},
                    "end": {"line": 1, "character": start + len("1 + 2")},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert result is not None
    actions = result["result"]
    assert any(action["kind"] == "quickfix" for action in actions)
