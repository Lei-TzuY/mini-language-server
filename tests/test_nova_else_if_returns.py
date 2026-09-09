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


def test_complete_else_if_chain_proves_value_return() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn value(left: Bool, right: Bool) -> Int { "
        "if (left) { return 1; } "
        "else if (right) { return 2; } "
        "else { return 3; } }\n"
    )
    open_nova(server, uri, text)

    assert missing_returns(server, uri) == []


def test_incomplete_else_if_chain_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn value(left: Bool, right: Bool) -> Int { "
        "if (left) { return 1; } "
        "else if (right) { return 2; } }\n"
    )
    open_nova(server, uri, text)

    assert len(missing_returns(server, uri)) == 1


def test_else_if_nested_return_still_gets_type_checked() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn value(left: Bool, right: Bool) -> Int { '
        'if (left) { return 1; } '
        'else if (right) { return "wrong"; } '
        'else { return 3; } }\n'
    )
    open_nova(server, uri, text)

    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    assert not any(item.code == "nova.missing-return" for item in snapshot.diagnostics)
    mismatches = [
        item for item in snapshot.diagnostics if item.code == "nova.return-type"
    ]
    assert len(mismatches) == 1
    assert text[mismatches[0].span.start : mismatches[0].span.end] == '"wrong"'


def test_did_change_rebinds_missing_return_to_complete_else_if_chain() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn value(left: Bool, right: Bool) -> Int { "
            "if (left) { return 1; } else if (right) { return 2; } }\n"
        ),
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
                            "fn value(left: Bool, right: Bool) -> Int { "
                            "if (left) { return 1; } "
                            "else if (right) { return 2; } "
                            "else { return 3; } }\n"
                        )
                    }
                ],
            },
        )
    )

    assert missing_returns(server, uri) == []


def test_close_reopen_rebuilds_else_if_return_proof() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn value() -> Int {}\n")
    assert len(missing_returns(server, uri)) == 1

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(
        server,
        uri,
        (
            "fn value(left: Bool, right: Bool) -> Int { "
            "if (left) { return 1; } "
            "else if (right) { return 2; } "
            "else { return 3; } }\n"
        ),
        version=1,
    )

    assert missing_returns(server, uri) == []


def test_same_version_replacement_uses_exact_else_if_semantics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn value(left: Bool, right: Bool) -> Int { "
        "if (left) { return 1; } "
        "else if (right) { return 2; } "
        "else { return 3; } }\n"
    )
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
    assert [
        item
        for item in diagnostic_snapshot.diagnostics
        if item.code == "nova.missing-return"
    ] == []
