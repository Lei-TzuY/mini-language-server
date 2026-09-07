from __future__ import annotations

from mini_language_server import NovaProductLanguageServer
from mini_language_server.return_types import ReturnTypeNovaFunctionAdapter


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert response is not None
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


def codes(server: NovaProductLanguageServer, uri: str) -> list[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item.code or "" for item in snapshot.diagnostics]


def return_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == "nova.return-type"]


def test_return_and_boolean_literals_are_not_unresolved_names() -> None:
    tree = ReturnTypeNovaFunctionAdapter.parse(
        "fn truth() -> Bool { return true } fn lie() -> Bool { return false }\n"
    )
    assert tree.unresolved_names == ()


def test_literal_return_mismatch_is_deterministic_and_exact_spanned() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn count() -> Int { return "wrong"; }\n'
    open_nova(server, uri, text)

    diagnostics = return_diagnostics(server, uri)
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.message == "return type mismatch: expected 'Int', got 'String'"
    assert text[diagnostic.span.start : diagnostic.span.end] == '"wrong"'


def test_matching_literal_return_types_remain_clean() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn i() -> Int { return 1; } fn s() -> String { return "ok"; } '
        "fn b() -> Bool { return true; }\n",
    )
    assert "nova.return-type" not in codes(server, uri)


def test_typed_parameter_return_reference_uses_exact_semantic_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn value(input: String) -> Int { return input; }\n"
    open_nova(server, uri, text)

    diagnostics = return_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == (
        "return type mismatch: expected 'Int', got 'String'"
    )
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "input"


def test_local_and_alias_return_references_reuse_bounded_type_knowledge() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn explicit() -> Int { let value: String = "bad"\nreturn value; }\n'
        'fn inferred() -> Int { let value = "bad"\nreturn value; }\n'
        'fn alias() -> Int { let first = "bad"\nlet second = first\nreturn second; }\n'
    )
    open_nova(server, uri, text)

    diagnostics = return_diagnostics(server, uri)
    assert len(diagnostics) == 3
    assert all("got 'String'" in diagnostic.message for diagnostic in diagnostics)
    assert [text[item.span.start : item.span.end] for item in diagnostics] == [
        "value",
        "value",
        "second",
    ]


def test_untyped_or_matching_return_references_are_not_guessed() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn matching(value: Int) -> Int { return value; }\n"
            "fn unknown(value) -> Int { return value; }\n"
        ),
    )
    assert "nova.return-type" not in codes(server, uri)


def test_did_change_replaces_return_diagnostic_on_new_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    bad = 'fn value() -> Int { return "bad"; }\n'
    good = "fn value() -> Int { return 7; }\n"
    open_nova(server, uri, bad)
    assert "nova.return-type" in codes(server, uri)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": good}],
            },
        )
    )
    assert "nova.return-type" not in codes(server, uri)


def test_did_change_rebinds_return_reference_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn value(input: String) -> Int { return input; }\n")
    assert "nova.return-type" in codes(server, uri)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {"text": "fn value(input: Int) -> Int { return input; }\n"}
                ],
            },
        )
    )
    assert "nova.return-type" not in codes(server, uri)


def test_close_reopen_rebuilds_return_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 'fn value() -> Int { return "bad"; }\n')
    assert "nova.return-type" in codes(server, uri)
    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, "fn value() -> Int { return 9; }\n", version=1)
    assert "nova.return-type" not in codes(server, uri)


def test_same_version_replacement_rebinds_return_diagnostic_to_exact_semantics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn value(input: String) -> Int { return input; }\n"
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
        if item.code == "nova.return-type"
    ]
    assert len(diagnostics) == 1
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "input"
