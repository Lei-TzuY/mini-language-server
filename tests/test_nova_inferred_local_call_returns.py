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


def test_unannotated_result_infers_through_local_direct_call() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn source() -> Int { return 1; } "
        "fn wrapper() { let result = source() return result; } "
        "fn main() { let value = wrapper() value }\n"
    )
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value: Int"


def test_unannotated_result_infers_through_local_alias_chain() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn source() -> String { return "x"; } '
        "fn wrapper() { let result = source() let alias = result return alias; } "
        "fn main() { let value = wrapper() value }\n"
    )
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value: String"


def test_cross_file_local_call_result_recomputes_after_change() -> None:
    server = initialized_server()
    source_uri = "file:///workspace/source.nova"
    wrapper_uri = "file:///workspace/wrapper.nova"
    main_uri = "file:///workspace/main.nova"
    wrapper = "fn wrapper() { let result = source() return result; }\n"
    main = "fn main() { let value = wrapper() value }\n"
    open_nova(server, source_uri, "fn source() -> Int { return 1; }\n")
    open_nova(server, wrapper_uri, wrapper)
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value: Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": source_uri, "version": 2},
                "contentChanges": [{"text": "fn source() -> Bool { return true; }\n"}],
            },
        )
    )
    assert hover_value(server, main_uri, main) == "variable value: Bool"


def test_local_call_cycle_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn a() { let result = b() return result; } "
        "fn b() { let result = a() return result; } "
        "fn main() { let value = a() value }\n"
    )
    open_nova(server, uri, text)

    assert hover_value(server, uri, text) == "variable value"


def test_local_call_inferred_result_flows_into_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn source() -> String { return "x"; } '
        "fn wrapper() { let result = source() return result; } "
        "fn sink(value: Int) {} "
        "fn relay() -> Int { return wrapper(); } "
        "fn main() { sink(wrapper()) }\n"
    )
    open_nova(server, uri, text)

    assert {"nova.argument-type", "nova.return-type"} <= diagnostic_codes(server, uri)
