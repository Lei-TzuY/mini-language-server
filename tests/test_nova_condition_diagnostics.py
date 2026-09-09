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


def diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return snapshot.diagnostics


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    return {item.code for item in diagnostics(server, uri)}


def test_if_condition_requires_bool_without_becoming_a_function_call() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main(input: Int) { if (input + 1) { let value = 1; } }\n",
    )

    codes = diagnostic_codes(server, uri)
    assert "nova.condition-type" in codes
    assert "nova.unresolved-function" not in codes
    assert "nova.ambiguous-function" not in codes


def test_if_condition_accepts_bounded_logical_and_comparison_expression() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main(input: Int) { if (input < 2 && !(input == 1)) { let value = 1; } }\n",
    )

    assert "nova.condition-type" not in diagnostic_codes(server, uri)


def test_unknown_if_condition_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { if (missing()) { let value = 1; } }\n")

    codes = diagnostic_codes(server, uri)
    assert "nova.condition-type" not in codes
    assert "nova.unresolved-function" in codes


def test_if_condition_scan_ignores_comments_and_strings() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn main() { let text = "if (1)"; // if (1)\n if (true) { let value = 1; } }\n',
    )

    condition_diagnostics = [
        item for item in diagnostics(server, uri) if item.code == "nova.condition-type"
    ]
    assert condition_diagnostics == []


def test_cross_file_if_condition_rebinds_on_change_close_and_reopen() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper_bool = "fn source() -> Bool { return true; }\n"
    helper_int = "fn source() -> Int { return 1; }\n"
    main = "fn main() { if (source()) { let value = 1; } }\n"

    open_nova(server, helper_uri, helper_bool, version=1)
    open_nova(server, main_uri, main, version=1)
    assert "nova.condition-type" not in diagnostic_codes(server, main_uri)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": helper_int}],
            },
        )
    )
    assert "nova.condition-type" in diagnostic_codes(server, main_uri)

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}})
    )
    assert "nova.condition-type" not in diagnostic_codes(server, main_uri)

    open_nova(server, helper_uri, helper_bool, version=3)
    assert "nova.condition-type" not in diagnostic_codes(server, main_uri)


def test_same_version_condition_reanalysis_rebinds_exact_semantic_parent() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { if (1) { let value = 1; } }\n"
    open_nova(server, uri, text, version=1)

    document = server.documents.get(uri)
    first_semantic = server.semantics.get(uri)
    first_diagnostic = server.diagnostics.get(uri)
    assert document is not None
    assert first_semantic is not None
    assert first_diagnostic is not None
    assert "nova.condition-type" in diagnostic_codes(server, uri)

    second_semantic = server.nova_adapter.publish(server, document)
    second_diagnostic = server.diagnostics.get(uri)
    assert second_semantic is not first_semantic
    assert second_semantic.version == first_semantic.version == 1
    assert second_diagnostic is not None
    assert second_diagnostic is not first_diagnostic
    assert second_diagnostic.semantic is second_semantic
    assert "nova.condition-type" in diagnostic_codes(server, uri)


def test_if_condition_diagnostic_is_deterministic_and_uses_condition_span() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { if ( 1 + 2 ) { let value = 1; } if (false) {} }\n"
    open_nova(server, uri, text)

    condition_diagnostics = [
        item for item in diagnostics(server, uri) if item.code == "nova.condition-type"
    ]
    assert len(condition_diagnostics) == 1
    diagnostic = condition_diagnostics[0]
    assert text[diagnostic.span.start : diagnostic.span.end] == "1 + 2"
    assert diagnostic.message == "condition type mismatch: expected 'Bool', got 'Int'"
