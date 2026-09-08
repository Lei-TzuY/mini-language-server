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


def hover_value(server: NovaProductLanguageServer, uri: str, text: str) -> str:
    offset = text.rindex("value")
    response = server.handle(
        request(
            "textDocument/hover",
            10,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": offset},
            },
        )
    )
    assert response is not None
    return response["result"]["contents"]["value"]


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return {
        diagnostic.code
        for diagnostic in snapshot.diagnostics
        if diagnostic.code is not None
    }


def test_same_file_inferred_wrapper_chain_propagates_result_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn source() { return 1; } "
        "fn middle() { return source(); } "
        "fn wrapper() { return middle(); } "
        "fn main() { let value = wrapper() value }\n"
    )
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value: Int"


def test_cross_file_inferred_wrapper_chain_recomputes_after_leaf_change() -> None:
    server = initialized_server()
    source_uri = "file:///workspace/source.nova"
    middle_uri = "file:///workspace/middle.nova"
    wrapper_uri = "file:///workspace/wrapper.nova"
    main_uri = "file:///workspace/main.nova"
    main = "fn main() { let value = wrapper() value }\n"

    open_nova(server, source_uri, "fn source() { return 1; }\n")
    open_nova(server, middle_uri, "fn middle() { return source(); }\n")
    open_nova(server, wrapper_uri, "fn wrapper() { return middle(); }\n")
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value: Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": source_uri, "version": 2},
                "contentChanges": [{"text": "fn source() { return true; }\n"}],
            },
        )
    )
    assert hover_value(server, main_uri, main) == "variable value: Bool"


def test_cross_file_inferred_wrapper_chain_rebinds_after_close_reopen() -> None:
    server = initialized_server()
    source_uri = "file:///workspace/source.nova"
    wrapper_uri = "file:///workspace/wrapper.nova"
    main_uri = "file:///workspace/main.nova"
    main = "fn main() { let value = wrapper() value }\n"

    open_nova(server, source_uri, 'fn source() { return "x"; }\n')
    open_nova(server, wrapper_uri, "fn wrapper() { return source(); }\n")
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value: String"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": source_uri}}))
    assert hover_value(server, main_uri, main) == "variable value"

    open_nova(server, source_uri, "fn source() { return true; }\n")
    assert hover_value(server, main_uri, main) == "variable value: Bool"


def test_inferred_wrapper_cycle_is_suppressed() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn left() { return right(); } "
        "fn right() { return left(); } "
        "fn main() { let value = left() value }\n"
    )
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value"


def test_inferred_wrapper_chain_rejects_ambiguous_nested_target() -> None:
    server = initialized_server()
    wrapper_uri = "file:///workspace/wrapper.nova"
    main_uri = "file:///workspace/main.nova"
    main = "fn main() { let value = wrapper() value }\n"

    open_nova(server, "file:///workspace/a.nova", "fn source() { return 1; }\n")
    open_nova(server, "file:///workspace/b.nova", "fn source() { return 1; }\n")
    open_nova(server, wrapper_uri, "fn wrapper() { return source(); }\n")
    open_nova(server, main_uri, main)

    assert hover_value(server, main_uri, main) == "variable value"


def test_transitive_inferred_result_flows_into_argument_and_return_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn source() { return "x"; } '
        "fn middle() { return source(); } "
        "fn wrapper() { return middle(); } "
        "fn sink(value: Int) {} "
        "fn relay() -> Int { return wrapper(); } "
        "fn main() { sink(wrapper()) }\n"
    )
    open_nova(server, uri, text)

    assert {"nova.argument-type", "nova.return-type"} <= diagnostic_codes(server, uri)
