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


def missing_returns(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == "nova.missing-return"]


def test_empty_typed_function_reports_deterministic_missing_return() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn value() -> Int {}\n"
    open_nova(server, uri, text)

    diagnostics = missing_returns(server, uri)
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.message == (
        "function 'value' with return type 'Int' has no top-level value return"
    )
    assert text[diagnostic.span.start : diagnostic.span.end] == "Int"


def test_bare_return_is_not_a_value_return() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn value() -> String { return; }\n"
    open_nova(server, uri, text)

    diagnostics = missing_returns(server, uri)
    assert len(diagnostics) == 1
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "String"


def test_value_return_and_never_return_annotation_remain_clean() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn value() -> Bool { return true; } fn diverge() -> ! {}\n",
    )
    assert missing_returns(server, uri) == []


def test_nested_if_return_does_not_prove_function_returns() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn value(flag: Bool) -> Int { if (flag) { return 1; } }\n"
    open_nova(server, uri, text)

    diagnostics = missing_returns(server, uri)
    assert len(diagnostics) == 1
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "Int"


def test_nested_while_return_does_not_prove_function_returns() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn value(flag: Bool) -> String { while (flag) { return \"ok\"; } }\n"
    open_nova(server, uri, text)

    assert len(missing_returns(server, uri)) == 1


def test_top_level_value_return_after_conditional_satisfies_bounded_check() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn value(flag: Bool) -> Int { if (flag) { return 1; } return 0; }\n"
    open_nova(server, uri, text)

    assert missing_returns(server, uri) == []


def test_nested_return_still_participates_in_return_type_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn value(flag: Bool) -> Int { if (flag) { return "wrong"; } }\n'
    open_nova(server, uri, text)

    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    diagnostics = snapshot.diagnostics
    assert any(item.code == "nova.missing-return" for item in diagnostics)
    mismatches = [item for item in diagnostics if item.code == "nova.return-type"]
    assert len(mismatches) == 1
    assert text[mismatches[0].span.start : mismatches[0].span.end] == '"wrong"'


def test_comments_do_not_fake_a_value_return() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn value() -> Int { // return 1\n let text = "return 2"\n }\n',
    )
    assert len(missing_returns(server, uri)) == 1


def test_did_change_clears_missing_return_on_new_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn value(flag: Bool) -> Int { if (flag) { return 1; } }\n",
    )
    assert len(missing_returns(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "text": (
                            "fn value(flag: Bool) -> Int { "
                            "if (flag) { return 1; } return 0; }\n"
                        )
                    }
                ],
            },
        )
    )
    assert missing_returns(server, uri) == []


def test_close_reopen_rebuilds_missing_return_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn value() -> Int {}\n")
    assert len(missing_returns(server, uri)) == 1

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, "fn value() -> Int { return 1; }\n", version=1)
    assert missing_returns(server, uri) == []


def test_same_version_replacement_rebinds_missing_return_to_exact_semantics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn value(flag: Bool) -> Int { if (flag) { return 1; } }\n"
    open_nova(server, uri, text)
    server.drain_notifications()
    original = server.workspace_symbols.get(uri)
    assert original is not None

    real_commit = server.workspace_symbols.commit_snapshots_if_current
    replaced = False

    def replace_then_commit(snapshots, callback):
        nonlocal replaced
        if not replaced:
            replaced = True
            document = server.documents.get(uri)
            assert document is not None
            replacement = server.nova_adapter.publish(server, document)
            server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    server._publish_workspace_diagnostics()

    current = server.workspace_symbols.get(uri)
    assert current is not None and current is not original
    diagnostic_snapshot = server.diagnostics.get(uri)
    assert diagnostic_snapshot is not None
    assert diagnostic_snapshot.semantic is current
    diagnostics = [
        item
        for item in diagnostic_snapshot.diagnostics
        if item.code == "nova.missing-return"
    ]
    assert len(diagnostics) == 1
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "Int"
