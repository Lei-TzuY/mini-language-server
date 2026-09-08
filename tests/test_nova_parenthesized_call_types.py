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


def test_parenthesized_explicit_call_result_reaches_return_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn source() -> String { return "value"; }\n'
        "fn main() -> Int { return ((source())); }\n",
    )

    assert "nova.return-type" in diagnostic_codes(server, uri)


def test_parenthesized_inferred_call_result_reaches_argument_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn source() { return "value"; }\n'
        "fn sink(value: Int) {}\n"
        "fn main() { sink(((source()))) }\n",
    )

    assert "nova.argument-type" in diagnostic_codes(server, uri)


def test_parenthesized_compound_call_expression_is_not_typed_as_direct_call() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn source() -> String { return "value"; }\n'
        "fn sink(value: Int) {}\n"
        "fn main() { sink((source() + 1)) }\n",
    )

    assert "nova.argument-type" not in diagnostic_codes(server, uri)


def test_parenthesized_cross_file_call_rebinds_after_did_change() -> None:
    server = initialized_server()
    source_uri = "file:///workspace/source.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, source_uri, 'fn source() -> String { return "value"; }\n')
    open_nova(server, main_uri, "fn main() -> Int { return ((source())); }\n")
    assert "nova.return-type" in diagnostic_codes(server, main_uri)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": source_uri, "version": 2},
                "contentChanges": [{"text": "fn source() -> Int { return 1; }\n"}],
            },
        )
    )

    assert "nova.return-type" not in diagnostic_codes(server, main_uri)
