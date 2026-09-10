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


def open_nova(
    server: NovaProductLanguageServer, uri: str, text: str, version: int = 1
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


def diagnostics_with_code(
    server: NovaProductLanguageServer, uri: str, code: str
):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == code]


def test_direct_break_arm_inside_while_does_not_need_assignment_before_join() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { var value: Int; while (true) { "
        "if flag { break; } else { value = 1; } let copy = value; break; } }\n"
    )
    open_nova(server, uri, text)

    assert diagnostics_with_code(server, uri, "nova.uninitialized-read") == []
    assert diagnostics_with_code(server, uri, "nova.invalid-loop-control") == []


def test_direct_continue_arm_inside_while_does_not_need_assignment_before_join() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { var value: Int; while (true) { "
        "if flag { continue; } else { value = 1; } let copy = value; break; } }\n"
    )
    open_nova(server, uri, text)

    assert diagnostics_with_code(server, uri, "nova.uninitialized-read") == []
    assert diagnostics_with_code(server, uri, "nova.invalid-loop-control") == []


def test_out_of_loop_break_does_not_prove_initialization_join() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { var value: Int; "
        "if flag { break; } else { value = 1; } let copy = value; }\n"
    )
    open_nova(server, uri, text)

    reads = diagnostics_with_code(server, uri, "nova.uninitialized-read")
    assert len(reads) == 1
    assert reads[0].span.start == text.index("value", text.index("copy"))
    assert len(diagnostics_with_code(server, uri, "nova.invalid-loop-control")) == 1


def test_nested_only_continue_does_not_terminate_enclosing_arm() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool, nested: Bool) { var value: Int; while (true) { "
        "if flag { if nested { continue; } } else { value = 1; } "
        "let copy = value; break; } }\n"
    )
    open_nova(server, uri, text)

    reads = diagnostics_with_code(server, uri, "nova.uninitialized-read")
    assert len(reads) == 1
    assert reads[0].span.start == text.index("value", text.index("copy"))


def test_did_change_rebinds_loop_exit_initialization_to_current_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = (
        "fn main(flag: Bool) { var value: Int; while (true) { "
        "if flag { let other = 0; } else { value = 1; } let copy = value; break; } }\n"
    )
    valid = (
        "fn main(flag: Bool) { var value: Int; while (true) { "
        "if flag { continue; } else { value = 1; } let copy = value; break; } }\n"
    )
    open_nova(server, uri, invalid, 1)
    assert len(diagnostics_with_code(server, uri, "nova.uninitialized-read")) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": valid}],
            },
        )
    )
    assert diagnostics_with_code(server, uri, "nova.uninitialized-read") == []
