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


def test_same_file_function_call_return_type_is_validated() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn helper() -> String { return "value"; }\n'
        "fn main() -> Int { return helper(); }\n"
    )
    open_nova(server, uri, text)

    mismatches = diagnostics(server, uri, "nova.return-type")
    assert len(mismatches) == 1
    assert mismatches[0].message == "return type mismatch: expected 'Int', got 'String'"
    assert text[mismatches[0].span.start : mismatches[0].span.end] == "helper()"


def test_unique_cross_file_function_call_return_type_is_validated() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "value"; }\n')
    main_text = "fn main() -> Bool { return helper(); }\n"
    open_nova(server, main_uri, main_text)

    mismatches = diagnostics(server, main_uri, "nova.return-type")
    assert len(mismatches) == 1
    assert mismatches[0].message == "return type mismatch: expected 'Bool', got 'String'"
    assert main_text[mismatches[0].span.start : mismatches[0].span.end] == "helper()"


def test_matching_cross_file_function_call_return_type_stays_clean() -> None:
    server = initialized_server()
    open_nova(server, "file:///workspace/helper.nova", "fn helper() -> Int { return 1; }\n")
    main_uri = "file:///workspace/main.nova"
    open_nova(server, main_uri, "fn main() -> Int { return helper(); }\n")

    assert diagnostics(server, main_uri, "nova.return-type") == []


def test_ambiguous_function_call_return_type_is_not_guessed() -> None:
    server = initialized_server()
    open_nova(server, "file:///workspace/a.nova", 'fn helper() -> String { return "a"; }\n')
    open_nova(server, "file:///workspace/b.nova", "fn helper() -> Bool { return true; }\n")
    main_uri = "file:///workspace/main.nova"
    open_nova(server, main_uri, "fn main() -> Int { return helper(); }\n")

    assert diagnostics(server, main_uri, "nova.return-type") == []
    assert len(diagnostics(server, main_uri, "nova.ambiguous-function")) == 1


def test_did_change_rebinds_cross_file_function_call_return_type() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "value"; }\n')
    open_nova(server, main_uri, "fn main() -> Int { return helper(); }\n")
    assert len(diagnostics(server, main_uri, "nova.return-type")) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": "fn helper() -> Int { return 1; }\n"}],
            },
        )
    )

    assert diagnostics(server, main_uri, "nova.return-type") == []


def test_close_reopen_rebinds_cross_file_function_call_return_type() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "value"; }\n')
    open_nova(server, main_uri, "fn main() -> Int { return helper(); }\n")
    assert len(diagnostics(server, main_uri, "nova.return-type")) == 1

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}}))
    assert diagnostics(server, main_uri, "nova.return-type") == []

    open_nova(server, helper_uri, "fn helper() -> Int { return 1; }\n", version=1)
    assert diagnostics(server, main_uri, "nova.return-type") == []


def test_same_version_workspace_replacement_suppresses_stale_call_return_publication() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, 'fn helper() -> String { return "value"; }\n')
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

    server.workspace_symbols.commit_snapshots_if_current = real_commit  # type: ignore[method-assign]
    server._publish_workspace_diagnostics()
    refreshed = server.diagnostics.get(main_uri)
    assert refreshed is not None and refreshed is not original_diagnostics
    assert len(diagnostics(server, main_uri, "nova.return-type")) == 1
