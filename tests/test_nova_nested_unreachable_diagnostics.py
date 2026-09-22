from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def open_nova(server: NovaProductLanguageServer, uri: str, text: str, version: int) -> None:
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


def unreachable_texts(server: NovaProductLanguageServer, uri: str) -> tuple[str, ...]:
    snapshot = server.diagnostics.get(uri)
    document = server.documents.get(uri)
    assert snapshot is not None
    assert document is not None
    return tuple(
        document.text[diagnostic.span.start : diagnostic.span.end]
        for diagnostic in snapshot.diagnostics
        if diagnostic.code == "nova.unreachable-code"
    )


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    return server


def test_nested_if_and_while_bodies_report_unreachable_suffixes() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) {\n"
        "  if (flag) { return; let in_if = 1; }\n"
        "  while (flag) { return; let in_while = 2; }\n"
        "}\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == (
        "let in_if = 1;",
        "let in_while = 2;",
    )


def test_loop_control_marks_only_loop_owned_suffixes_unreachable() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) {\n"
        "  break; let outside = 0;\n"
        "  while (flag) { break; let after_break = 1; }\n"
        "  while (flag) { if (flag) { continue; let after_continue = 2; } }\n"
        "}\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == (
        "let after_break = 1;",
        "let after_continue = 2;",
    )


def test_nested_loop_control_does_not_kill_outer_loop_body() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) {\n"
        "  while (flag) {\n"
        "    if (flag) { break; let nested_dead = 1; }\n"
        "    let outer_live = 2;\n"
        "  }\n"
        "}\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == ("let nested_dead = 1;",)


def test_nested_branch_complete_statement_marks_its_body_suffix_unreachable() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(a: Bool, b: Bool) {\n"
        "  if (a) {\n"
        "    if (b) { return; } else { return; }\n"
        "    let dead = 1;\n"
        "  }\n"
        "}\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == ("let dead = 1;",)


def test_outer_unreachable_suffix_does_not_emit_nested_duplicate_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { return; "
        "if (flag) { return; let nested = 1; } let dead = 2; }\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == (
        "if (flag) { return; let nested = 1; } let dead = 2;",
    )


def test_nested_unreachable_rebinds_across_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    dead = "fn main(flag: Bool) { if (flag) { return; let dead = 1; } }\n"
    live = "fn main(flag: Bool) { if (flag) { let live = 1; } }\n"
    open_nova(server, uri, dead, 1)
    assert unreachable_texts(server, uri) == ("let dead = 1;",)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": live}],
            },
        )
    )
    assert unreachable_texts(server, uri) == ()

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None
    open_nova(server, uri, dead, 3)
    assert unreachable_texts(server, uri) == ("let dead = 1;",)

def test_constant_true_loop_without_reachable_break_kills_following_suffix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() { while (1 < 2) { continue; } "
        "let dead = 1; let also_dead = 2; }\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == (
        "let dead = 1; let also_dead = 2;",
    )


def test_reachable_break_keeps_suffix_after_constant_true_loop_live() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { while (true) { break; } let live = 1; }\n"
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == ()


def test_break_in_proven_dead_branch_does_not_make_true_loop_fall_through() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() { while (true) { if (false) { break; } continue; } "
        "let dead = 1; }\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == (
        "break;",
        "let dead = 1;",
    )


def test_break_under_unknown_branch_keeps_true_loop_fallthrough_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { while (true) { if (flag) { break; } } "
        "let maybe_live = 1; }\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == ()


def test_break_in_nested_loop_does_not_exit_constant_true_outer_loop() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { while (true) { "
        "while (flag) { break; } continue; } let dead = 1; }\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == ("let dead = 1;",)


def test_nested_constant_true_loop_kills_only_its_enclosing_body_suffix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { if (flag) { "
        "while (true) { continue; } let dead = 1; } "
        "let outer_live = 2; }\n"
    )
    open_nova(server, uri, text, 1)

    assert unreachable_texts(server, uri) == ("let dead = 1;",)
