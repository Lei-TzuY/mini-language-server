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


def local_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == "nova.local-type"]


def test_literal_local_type_mismatches_are_deterministic_and_exact_spanned() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn main() { let count: Int = "bad"\n'
        "let name: String = true\n"
        "let ready: Bool = 7\n}"
    )
    open_nova(server, uri, text)

    diagnostics = local_diagnostics(server, uri)
    assert [item.message for item in diagnostics] == [
        "local type mismatch: expected 'Int', got 'String'",
        "local type mismatch: expected 'String', got 'Bool'",
        "local type mismatch: expected 'Bool', got 'Int'",
    ]
    assert [text[item.span.start : item.span.end] for item in diagnostics] == [
        '"bad"',
        "true",
        "7",
    ]


def test_matching_and_unknown_local_initializer_types_remain_clean() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            'fn main(input) { let count: Int = 7\n'
            'let name: String = "ok"\n'
            "let ready: Bool = false\n"
            "let opaque: Int = input\n}"
        ),
    )
    assert local_diagnostics(server, uri) == []


def test_typed_parameter_initializer_uses_exact_semantic_reference() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: String) { let count: Int = input\n}\n"
    open_nova(server, uri, text)

    diagnostics = local_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "local type mismatch: expected 'Int', got 'String'"
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "input"


def test_local_alias_initializer_reuses_bounded_exact_type_knowledge() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn main() { let source = "bad"\n'
        "let count: Int = source\n}"
    )
    open_nova(server, uri, text)

    diagnostics = local_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "source"


def test_same_file_function_call_initializer_uses_bounded_return_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn helper() -> String { return "value"; }\n'
        "fn main() { let count: Int = helper()\n}\n"
    )
    open_nova(server, uri, text)

    diagnostics = local_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "local type mismatch: expected 'Int', got 'String'"
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "helper()"


def test_unique_cross_file_function_call_initializer_is_validated() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "value"; }\n')
    main_text = "fn main() { let ready: Bool = helper()\n}\n"
    open_nova(server, main_uri, main_text)

    diagnostics = local_diagnostics(server, main_uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "local type mismatch: expected 'Bool', got 'String'"
    assert main_text[diagnostics[0].span.start : diagnostics[0].span.end] == "helper()"


def test_matching_cross_file_function_call_initializer_stays_clean() -> None:
    server = initialized_server()
    open_nova(server, "file:///workspace/helper.nova", "fn helper() -> Int { return 1; }\n")
    main_uri = "file:///workspace/main.nova"
    open_nova(server, main_uri, "fn main() { let count: Int = helper()\n}\n")

    assert local_diagnostics(server, main_uri) == []


def test_ambiguous_function_call_initializer_type_is_not_guessed() -> None:
    server = initialized_server()
    open_nova(server, "file:///workspace/a.nova", 'fn helper() -> String { return "a"; }\n')
    open_nova(server, "file:///workspace/b.nova", "fn helper() -> Bool { return true; }\n")
    main_uri = "file:///workspace/main.nova"
    open_nova(server, main_uri, "fn main() { let count: Int = helper()\n}\n")

    assert local_diagnostics(server, main_uri) == []
    snapshot = server.diagnostics.get(main_uri)
    assert snapshot is not None
    ambiguous = [
        item for item in snapshot.diagnostics if item.code == "nova.ambiguous-function"
    ]
    assert len(ambiguous) == 1


def test_did_change_replaces_local_type_diagnostic_on_new_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 'fn main() { let count: Int = "bad"\n}\n')
    assert len(local_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { let count: Int = 7\n}\n"}],
            },
        )
    )
    assert local_diagnostics(server, uri) == []


def test_did_change_rebinds_cross_file_function_call_initializer_type() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "value"; }\n')
    open_nova(server, main_uri, "fn main() { let count: Int = helper()\n}\n")
    assert len(local_diagnostics(server, main_uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": "fn helper() -> Int { return 1; }\n"}],
            },
        )
    )

    assert local_diagnostics(server, main_uri) == []


def test_close_reopen_rebuilds_local_type_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 'fn main() { let count: Int = "bad"\n}\n')
    assert len(local_diagnostics(server, uri)) == 1

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, "fn main() { let count: Int = 7\n}\n", version=1)
    assert local_diagnostics(server, uri) == []


def test_close_reopen_rebinds_cross_file_function_call_initializer_type() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "value"; }\n')
    open_nova(server, main_uri, "fn main() { let count: Int = helper()\n}\n")
    assert len(local_diagnostics(server, main_uri)) == 1

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}}))
    assert local_diagnostics(server, main_uri) == []

    open_nova(server, helper_uri, "fn helper() -> Int { return 1; }\n", version=1)
    assert local_diagnostics(server, main_uri) == []


def test_same_version_replacement_rebinds_local_diagnostic_to_exact_semantics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn main() { let count: Int = "bad"\n}\n'
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
    assert len(local_diagnostics(server, uri)) == 1


def test_same_version_workspace_replacement_suppresses_stale_local_call_publication() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "value"; }\n')
    open_nova(server, main_uri, "fn main() { let count: Int = helper()\n}\n")
    original_helper = server.workspace_symbols.get(helper_uri)
    original_diagnostics = server.diagnostics.get(main_uri)
    assert original_helper is not None
    assert original_diagnostics is not None
    assert len(local_diagnostics(server, main_uri)) == 1

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

    server.workspace_symbols.commit_snapshots_if_current = real_commit  # type: ignore[method-assign]
    server._publish_workspace_diagnostics()
    refreshed = server.diagnostics.get(main_uri)
    assert refreshed is not None and refreshed is not original_diagnostics
    assert len(local_diagnostics(server, main_uri)) == 1
