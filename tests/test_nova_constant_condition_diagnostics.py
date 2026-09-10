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


def constant_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return tuple(
        diagnostic
        for diagnostic in snapshot.diagnostics
        if diagnostic.code == "nova.constant-condition"
    )


def test_literal_if_and_while_conditions_are_reported_with_exact_spans() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() { if ( true ) { let first = 1; } "
        "while (((false))) { let second = 2; } }\n"
    )
    open_nova(server, uri, text)

    diagnostics = constant_diagnostics(server, uri)
    assert [text[item.span.start : item.span.end] for item in diagnostics] == [
        "true",
        "false",
    ]
    assert [item.message for item in diagnostics] == [
        "if condition is always true",
        "while condition is always false",
    ]


def test_nonliteral_bool_condition_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main(flag: Bool) { if (flag) { let value = 1; } while (!flag) {} }\n",
    )

    assert constant_diagnostics(server, uri) == ()


def test_constant_condition_scan_ignores_comments_and_strings() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            'fn main() { let text = "if (false)"; // while (true)\n'
            " if (false) { let value = 1; } }\n"
        ),
    )

    diagnostics = constant_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "if condition is always false"


def test_did_change_rebinds_constant_condition_to_current_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main(flag: Bool) { if (false) {} }\n", version=1)
    first = server.diagnostics.get(uri)
    assert first is not None
    assert len(constant_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main(flag: Bool) { if (flag) {} }\n"}],
            },
        )
    )

    second = server.diagnostics.get(uri)
    assert second is not None
    assert second is not first
    assert second.semantic.symbols.syntax.document.version == 2
    assert constant_diagnostics(server, uri) == ()


def test_same_version_reanalysis_rebinds_exact_semantic_parent() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { while (true) {} }\n"
    open_nova(server, uri, text, version=1)

    document = server.documents.get(uri)
    first_semantic = server.semantics.get(uri)
    first_diagnostic = server.diagnostics.get(uri)
    assert document is not None
    assert first_semantic is not None
    assert first_diagnostic is not None
    assert len(constant_diagnostics(server, uri)) == 1

    second_semantic = server.nova_adapter.publish(server, document)
    second_diagnostic = server.diagnostics.get(uri)
    assert second_semantic is not first_semantic
    assert second_semantic.version == first_semantic.version == 1
    assert second_diagnostic is not None
    assert second_diagnostic is not first_diagnostic
    assert second_diagnostic.semantic is second_semantic
    assert len(constant_diagnostics(server, uri)) == 1


def test_close_reopen_republishes_against_new_snapshot_identity() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { if (true) {} }\n"
    open_nova(server, uri, text, version=1)
    first = server.diagnostics.get(uri)
    assert first is not None

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None

    open_nova(server, uri, text, version=3)
    second = server.diagnostics.get(uri)
    assert second is not None
    assert second is not first
    assert second.semantic is not first.semantic
    assert len(constant_diagnostics(server, uri)) == 1
