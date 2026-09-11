from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"codeAction": {}}}},
        )
    )
    assert response is not None
    return server


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
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


def codes(server: NovaProductLanguageServer, uri: str) -> list[str]:
    return [item.code or "" for item in diagnostics(server, uri)]


def return_diagnostics(server: NovaProductLanguageServer, uri: str):
    return [item for item in diagnostics(server, uri) if item.code == "nova.return-type"]


def code_action(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    start: int,
    end: int,
) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": start},
                    "end": {"line": 0, "character": end},
                },
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert result is not None
    return result


def test_matching_literal_tail_satisfies_explicit_return_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn value() -> Int { 7 }\n")

    assert "nova.missing-return" not in codes(server, uri)
    assert "nova.return-type" not in codes(server, uri)


def test_mismatched_literal_tail_reports_exact_return_type_span() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn value() -> Int { "bad" }\n'
    open_nova(server, uri, text)

    found = return_diagnostics(server, uri)
    assert len(found) == 1
    assert found[0].message == "return type mismatch: expected 'Int', got 'String'"
    assert text[found[0].span.start : found[0].span.end] == '"bad"'
    assert "nova.missing-return" not in codes(server, uri)


def test_typed_reference_and_unit_tails_reuse_exact_semantic_types() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn wrong(value: String) -> Int { value }\n"
        "fn identity(value: Int) -> Int { value }\n"
        "fn done() -> Unit { () }\n"
    )
    open_nova(server, uri, text)

    found = return_diagnostics(server, uri)
    assert len(found) == 1
    assert found[0].message == "return type mismatch: expected 'Int', got 'String'"
    assert text[found[0].span.start : found[0].span.end] == "value"
    assert "nova.missing-return" not in codes(server, uri)


def test_cross_file_call_tail_uses_exact_workspace_result_type() -> None:
    server = initialized_server()
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, library_uri, 'fn source() -> String { "ok" }\n')
    text = "fn value() -> Int { source() }\n"
    open_nova(server, main_uri, text)

    found = return_diagnostics(server, main_uri)
    assert len(found) == 1
    assert found[0].message == "return type mismatch: expected 'Int', got 'String'"
    assert text[found[0].span.start : found[0].span.end] == "source()"
    assert "nova.missing-return" not in codes(server, main_uri)


def test_unknown_tail_type_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn value(input) -> Int { input }\n")

    assert "nova.return-type" not in codes(server, uri)
    assert "nova.missing-return" in codes(server, uri)


def test_did_change_rebinds_tail_validation_to_new_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    bad = 'fn value() -> Int { "bad" }\n'
    good = "fn value() -> Int { 9 }\n"
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
    assert "nova.missing-return" not in codes(server, uri)


def test_close_reopen_rebuilds_tail_validation_from_new_document_identity() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 'fn value() -> Int { "bad" }\n')
    assert "nova.return-type" in codes(server, uri)

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, "fn value() -> Int { 3 }\n", version=1)
    assert "nova.return-type" not in codes(server, uri)
    assert "nova.missing-return" not in codes(server, uri)


def test_same_version_workspace_replacement_rebinds_tail_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn value() -> Int { "bad" }\n'
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
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    assert snapshot.semantic is current
    found = [item for item in snapshot.diagnostics if item.code == "nova.return-type"]
    assert len(found) == 1
    assert text[found[0].span.start : found[0].span.end] == '"bad"'


def test_tail_mismatch_reuses_exact_snapshot_return_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn value() -> Int { "bad" }\n'
    open_nova(server, uri, text)
    start = text.index('"bad"')

    actions = code_action(server, uri, 2, start, start + len('"bad"'))["result"]
    assert len(actions) == 1
    assert actions[0]["title"] == "Replace return expression with Int literal"
    edit = actions[0]["edit"]["changes"][uri][0]
    assert edit["newText"] == "0"


def test_tail_quick_fix_honors_cancellation_checkpoint() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn value() -> Int { "bad" }\n'
    open_nova(server, uri, text)
    start = text.index('"bad"')
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert code_action(server, uri, 2, start, start + len('"bad"')) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
