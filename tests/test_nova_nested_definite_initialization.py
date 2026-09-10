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


def uninitialized_reads(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == "nova.uninitialized-read"]


def test_nested_complete_conditional_proves_outer_if_else_join() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(left: Bool, nested: Bool) { var value: Int; "
        "if left { if nested { value = 1; } else { value = 2; } } "
        "else { value = 3; } let copy = value; }\n"
    )
    open_nova(server, uri, text)
    assert uninitialized_reads(server, uri) == []


def test_nested_complete_else_if_chain_proves_outer_join() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(left: Bool, first: Bool, second: Bool) { var value: Int; "
        "if left { if first { value = 1; } else if second { value = 2; } "
        "else { value = 3; } } else { value = 4; } let copy = value; }\n"
    )
    open_nova(server, uri, text)
    assert uninitialized_reads(server, uri) == []


def test_incomplete_nested_conditional_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(left: Bool, nested: Bool) { var value: Int; "
        "if left { if nested { value = 1; } } else { value = 2; } "
        "let copy = value; }\n"
    )
    open_nova(server, uri, text)

    diagnostics = uninitialized_reads(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].span.start == text.index("value", text.index("copy"))


def test_did_change_rebinds_nested_conditional_proof_to_current_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = (
        "fn main(left: Bool, nested: Bool) { var value: Int; "
        "if left { if nested { value = 1; } } else { value = 2; } "
        "let copy = value; }\n"
    )
    valid = (
        "fn main(left: Bool, nested: Bool) { var value: Int; "
        "if left { if nested { value = 1; } else { value = 3; } } "
        "else { value = 2; } let copy = value; }\n"
    )
    open_nova(server, uri, invalid, 1)
    assert len(uninitialized_reads(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": valid}],
            },
        )
    )
    assert uninitialized_reads(server, uri) == []
