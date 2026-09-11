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


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": text,
                }
            },
        )
    )


def diagnostics(server: NovaProductLanguageServer, uri: str, code: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == code]


def test_bare_return_infers_unit_for_exact_call_result() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn noop() { return; }\n"
        "fn consume(value: Int) -> Int { return value; }\n"
        "fn main() -> Int { consume(noop()); return 0; }\n"
    )
    open_nova(server, uri, text)

    items = diagnostics(server, uri, "nova.argument-type")
    assert len(items) == 1
    assert items[0].message == "argument 1 to 'consume' has type 'Unit'; expected 'Int'"
    assert text[items[0].span.start : items[0].span.end] == "noop()"


def test_bare_return_unit_inference_propagates_cross_file() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn noop() { return; }\n")
    open_nova(server, main_uri, "fn main() -> Int { return noop(); }\n")

    items = diagnostics(server, main_uri, "nova.return-type")
    assert len(items) == 1
    assert items[0].message == "return type mismatch: expected 'Int', got 'Unit'"


def test_mixed_bare_and_value_returns_remain_unknown() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn helper(flag: Bool) { if flag { return; } return 1; }\n"
        "fn unitish(flag: Bool) { if flag { return; } return (); }\n"
        "fn consume(value: String) -> Int { return 1; }\n"
        "fn main() -> Int { consume(helper(true)); consume(unitish(true)); return 0; }\n",
    )

    assert diagnostics(server, uri, "nova.argument-type") == []


def test_no_return_fallthrough_is_not_inferred_as_unit() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn helper() { }\n"
        "fn consume(value: Int) -> Int { return value; }\n"
        "fn main() -> Int { consume(helper()); return 0; }\n",
    )

    assert diagnostics(server, uri, "nova.argument-type") == []


def test_did_change_rebinds_bare_return_inference() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn helper() { return; } fn main() -> Int { return helper(); }\n",
    )
    original = server.diagnostics.get(uri)
    assert original is not None
    assert len(diagnostics(server, uri, "nova.return-type")) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "text": "fn helper() { return 1; } fn main() -> Int { return helper(); }\n"
                    }
                ],
            },
        )
    )

    current = server.diagnostics.get(uri)
    assert current is not None and current is not original
    assert diagnostics(server, uri, "nova.return-type") == []


def test_close_reopen_rebinds_bare_return_inference() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn helper() { return; } fn main() -> Int { return helper(); }\n"
    open_nova(server, uri, text)
    original = server.diagnostics.get(uri)
    assert original is not None

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, text)

    current = server.diagnostics.get(uri)
    assert current is not None and current is not original
    assert len(diagnostics(server, uri, "nova.return-type")) == 1


def test_same_version_workspace_replacement_suppresses_stale_bare_return_result() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn helper() { return; }\n")
    open_nova(server, main_uri, "fn main() -> Int { return helper(); }\n")
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
