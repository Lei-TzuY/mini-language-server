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


def test_top_level_return_marks_remaining_executable_suffix_unreachable() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { return; let value = 1; let other = 2; }\n"
    open_nova(server, uri, text)

    diagnostics = unreachable(server, uri)
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert text[diagnostic.span.start : diagnostic.span.end] == (
        "let value = 1; let other = 2;"
    )
    assert diagnostic.message == "unreachable code after guaranteed return"


def test_branch_complete_if_else_marks_following_code_unreachable() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { if (flag) { return; } else { return; } "
        "let value = 1; }\n"
    )
    open_nova(server, uri, text)

    diagnostics = unreachable(server, uri)
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert text[diagnostic.span.start : diagnostic.span.end] == "let value = 1;"


def test_incomplete_if_branch_remains_reachable() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(flag: Bool) { if (flag) { return; } let value = 1; }\n"
    open_nova(server, uri, text)

    assert unreachable(server, uri) == ()


def test_unreachable_scan_ignores_return_text_in_comments_and_strings() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn main() { let text = "return; let hidden = 1;"; '
        "// return; let hidden = 2;\n let value = 1; }\n"
    )
    open_nova(server, uri, text)

    assert unreachable(server, uri) == ()


def test_unreachable_diagnostic_rebinds_on_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    unreachable_text = "fn main() { return; let value = 1; }\n"
    reachable_text = "fn main() { let value = 1; }\n"
    open_nova(server, uri, unreachable_text, version=1)
    assert len(unreachable(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": reachable_text}],
            },
        )
    )
    assert unreachable(server, uri) == ()

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None

    open_nova(server, uri, unreachable_text, version=3)
    assert len(unreachable(server, uri)) == 1


def test_same_version_unreachable_reanalysis_rebinds_exact_semantic_parent() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { return; let value = 1; }\n"
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
