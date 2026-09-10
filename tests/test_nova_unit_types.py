from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert response is not None
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


def diagnostics(server: NovaProductLanguageServer, uri: str, code: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == code]


def test_unit_literal_participates_in_argument_type_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn consume(value: Unit) -> Int { return 1; }\n"
        "fn main() -> Int { consume(()); return 0; }\n",
    )

    assert diagnostics(server, uri, "nova.argument-type") == []


def test_unit_literal_reports_argument_and_return_mismatches() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn consume(value: Int) -> Int { return value; }\n"
        "fn main() -> Int { consume(()); return (); }\n"
    )
    open_nova(server, uri, text)

    arguments = diagnostics(server, uri, "nova.argument-type")
    assert len(arguments) == 1
    assert arguments[0].message == "argument 1 to 'consume' has type 'Unit'; expected 'Int'"
    assert text[arguments[0].span.start : arguments[0].span.end] == "()"

    returns = diagnostics(server, uri, "nova.return-type")
    assert len(returns) == 1
    assert returns[0].message == "return type mismatch: expected 'Int', got 'Unit'"
    assert text[returns[0].span.start : returns[0].span.end] == "()"


def test_explicit_unit_function_may_return_unit_or_fall_through() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn explicit() -> Unit { return (); }\n"
        "fn fallthrough() -> Unit { }\n",
    )

    assert diagnostics(server, uri, "nova.return-type") == []
    assert diagnostics(server, uri, "nova.missing-return") == []


def test_explicit_unit_call_result_propagates_across_workspace() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn noop() -> Unit { return (); }\n")
    main_text = "fn main() -> Int { return noop(); }\n"
    open_nova(server, main_uri, main_text)

    returns = diagnostics(server, main_uri, "nova.return-type")
    assert len(returns) == 1
    assert returns[0].message == "return type mismatch: expected 'Int', got 'Unit'"
    assert main_text[returns[0].span.start : returns[0].span.end] == "noop()"


def test_unit_literal_participates_in_local_and_assignment_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() -> Unit {\n"
        "  let ok: Unit = ();\n"
        "  let bad: Unit = 1;\n"
        "  var target: Unit = ();\n"
        "  target = 1;\n"
        "  return ();\n"
        "}\n"
    )
    open_nova(server, uri, text)

    locals_ = diagnostics(server, uri, "nova.local-type")
    assert len(locals_) == 1
    assert locals_[0].message == "local type mismatch: expected 'Unit', got 'Int'"

    assignments = diagnostics(server, uri, "nova.assignment-type")
    assert len(assignments) == 1
    assert assignments[0].message == "assignment type mismatch: expected 'Unit', got 'Int'"


def test_did_change_rebinds_unit_literal_typing_to_current_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Int { return (); }\n")
    original = server.diagnostics.get(uri)
    assert original is not None
    assert len(diagnostics(server, uri, "nova.return-type")) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() -> Int { return 1; }\n"}],
            },
        )
    )

    current = server.diagnostics.get(uri)
    assert current is not None and current is not original
    assert diagnostics(server, uri, "nova.return-type") == []


def test_same_version_workspace_replacement_suppresses_stale_unit_call_result() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn noop() -> Unit { return (); }\n")
    open_nova(server, main_uri, "fn main() -> Int { return noop(); }\n")
    original_helper = server.workspace_symbols.get(helper_uri)
    original_diagnostics = server.diagnostics.get(main_uri)
    assert original_helper is not None
    assert original_diagnostics is not None
    assert len(diagnostics(server, main_uri, "nova.return-type")) == 1

    real_commit = server.workspace_symbols.commit_snapshots_if_current
    replaced = False

    def replace_then_commit(snapshots, callback):
        nonlocal replaced
        if not replaced:
            replaced = True
            document = server.documents.get(helper_uri)
            assert document is not None
            replacement = server.nova_adapter.publish(server, document)
            server.workspace_symbols.replace(replacement, expected=original_helper)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    server._publish_workspace_diagnostics()

    current_helper = server.workspace_symbols.get(helper_uri)
    assert current_helper is not None and current_helper is not original_helper
    assert server.diagnostics.get(main_uri) is original_diagnostics
