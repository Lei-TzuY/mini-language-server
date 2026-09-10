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
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    version: int = 1,
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


def unreachable(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return tuple(
        diagnostic
        for diagnostic in snapshot.diagnostics
        if diagnostic.code == "nova.unreachable-code"
    )


def test_constant_conditions_mark_only_structurally_dead_bodies() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() { "
        "if (false) { let first = 1; } "
        "if (true) { let live = 2; } else { let second = 3; } "
        "while (((false))) { let third = 4; } "
        "}\n"
    )
    open_nova(server, uri, text)

    diagnostics = unreachable(server, uri)
    assert [text[item.span.start : item.span.end] for item in diagnostics] == [
        "let first = 1;",
        "let second = 3;",
        "let third = 4;",
    ]
    assert [item.message for item in diagnostics] == [
        "unreachable code in constant-false if body",
        "unreachable code in constant-true else body",
        "unreachable code in constant-false while body",
    ]
    assert all(item.tags == ("unnecessary",) for item in diagnostics)


def test_nonliteral_and_constant_true_while_bodies_remain_reachable() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn main(flag: Bool) { "
            "if (flag) { let first = 1; } "
            "while (true) { break; } "
            "while (flag) { let second = 2; } "
            "}\n"
        ),
    )

    assert unreachable(server, uri) == ()


def test_dead_branch_scan_ignores_control_flow_text_in_comments_and_strings() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            'fn main() { let text = "if (false) { let hidden = 1; }"; '
            "// while (false) { let hidden = 2; }\n"
            "if (false) { let visible = 3; } }\n"
        ),
    )

    diagnostics = unreachable(server, uri)
    assert len(diagnostics) == 1
    document = server.documents.get(uri)
    assert document is not None
    assert document.text[diagnostics[0].span.start : diagnostics[0].span.end] == (
        "let visible = 3;"
    )


def test_did_change_rebinds_dead_branch_diagnostic_to_current_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { if (false) { let value = 1; } }\n", version=1)
    first = server.diagnostics.get(uri)
    assert first is not None
    assert len(unreachable(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {"text": "fn main(flag: Bool) { if (flag) { let value = 1; } }\n"}
                ],
            },
        )
    )

    second = server.diagnostics.get(uri)
    assert second is not None
    assert second is not first
    assert second.semantic.symbols.syntax.document.version == 2
    assert unreachable(server, uri) == ()


def test_same_version_reanalysis_rebinds_dead_branch_to_exact_semantic_parent() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { while (false) { let value = 1; } }\n"
    open_nova(server, uri, text, version=1)

    document = server.documents.get(uri)
    first_semantic = server.semantics.get(uri)
    first_diagnostic = server.diagnostics.get(uri)
    assert document is not None
    assert first_semantic is not None
    assert first_diagnostic is not None
    assert len(unreachable(server, uri)) == 1

    second_semantic = server.nova_adapter.publish(server, document)
    second_diagnostic = server.diagnostics.get(uri)
    assert second_semantic is not first_semantic
    assert second_semantic.version == first_semantic.version == 1
    assert second_diagnostic is not None
    assert second_diagnostic is not first_diagnostic
    assert second_diagnostic.semantic is second_semantic
    assert len(unreachable(server, uri)) == 1


def test_close_reopen_republishes_dead_branch_against_new_snapshot_identity() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { if (true) {} else { let value = 1; } }\n"
    open_nova(server, uri, text, version=1)
    first = server.diagnostics.get(uri)
    assert first is not None
    assert len(unreachable(server, uri)) == 1

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None

    open_nova(server, uri, text, version=3)
    second = server.diagnostics.get(uri)
    assert second is not None
    assert second is not first
    assert second.semantic is not first.semantic
    assert len(unreachable(server, uri)) == 1
