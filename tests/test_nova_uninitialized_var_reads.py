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


def test_typed_var_read_before_first_assignment_is_diagnosed_at_exact_reference() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { var value: Int; let copy = value; value = 1; }\n"
    open_nova(server, uri, text)

    diagnostics = uninitialized_reads(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "local 'value' is read before its first assignment"
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "value"
    assert diagnostics[0].span.start == text.index("value", text.index("copy"))


def test_assignment_before_read_suppresses_uninitialized_read_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { var value: Int; value = 1; let copy = value; }\n")
    assert uninitialized_reads(server, uri) == []


def test_nested_assignment_does_not_initialize_later_outer_read() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { var value: Int; "
        "if flag { value = 1; } let copy = value; }\n"
    )
    open_nova(server, uri, text)

    diagnostics = uninitialized_reads(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].span.start == text.index("value", text.index("copy"))


def test_nested_assignment_initializes_later_read_in_same_branch() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { var value: Int; "
        "if flag { value = 1; let copy = value; } }\n"
    )
    open_nova(server, uri, text)
    assert uninitialized_reads(server, uri) == []


def test_outer_assignment_initializes_read_in_nested_branch() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { var value: Int; value = 1; "
        "if flag { let copy = value; } }\n"
    )
    open_nova(server, uri, text)
    assert uninitialized_reads(server, uri) == []


def test_if_else_assignments_initialize_later_outer_read() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { var value: Int; "
        "if flag { value = 1; } else { value = 2; } let copy = value; }\n"
    )
    open_nova(server, uri, text)
    assert uninitialized_reads(server, uri) == []


def test_if_without_else_assignment_does_not_initialize_later_outer_read() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { var value: Int; "
        "if flag { value = 1; } else { let other = 2; } let copy = value; }\n"
    )
    open_nova(server, uri, text)

    diagnostics = uninitialized_reads(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].span.start == text.index("value", text.index("copy"))


def test_nested_conditional_assignment_in_arm_does_not_prove_if_else_join() -> None:
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


def test_multiple_reads_before_first_assignment_are_deterministic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { var value: Int; let first = value; let second = value; value = 1; }\n"
    open_nova(server, uri, text)

    diagnostics = uninitialized_reads(server, uri)
    assert len(diagnostics) == 2
    assert [text[item.span.start : item.span.end] for item in diagnostics] == ["value", "value"]
    assert diagnostics[0].span.start < diagnostics[1].span.start


def test_did_change_rebinds_uninitialized_read_to_current_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = "fn main() { var value: Int; let copy = value; value = 1; }\n"
    valid = "fn main() { var value: Int; value = 1; let copy = value; }\n"
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


def test_did_change_rebinds_branch_scope_definite_initialization() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = (
        "fn main(flag: Bool) { var value: Int; "
        "if flag { value = 1; } let copy = value; }\n"
    )
    valid = (
        "fn main(flag: Bool) { var value: Int; value = 1; "
        "if flag { let copy = value; } }\n"
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


def test_did_change_rebinds_if_else_join_to_current_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = (
        "fn main(flag: Bool) { var value: Int; "
        "if flag { value = 1; } else { let other = 2; } let copy = value; }\n"
    )
    valid = (
        "fn main(flag: Bool) { var value: Int; "
        "if flag { value = 1; } else { value = 2; } let copy = value; }\n"
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
