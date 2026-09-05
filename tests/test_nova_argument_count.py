from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None
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


def latest_diagnostics(server: NovaProductLanguageServer, uri: str) -> list[dict]:
    notifications = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "textDocument/publishDiagnostics"
        and item.get("params", {}).get("uri") == uri
    ]
    assert notifications
    return notifications[-1]["params"]["diagnostics"]


def latest_codes(server: NovaProductLanguageServer, uri: str) -> list[str]:
    return [item["code"] for item in latest_diagnostics(server, uri)]


def test_same_file_call_reports_argument_count_mismatch() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn target(left: Int, right: Int) {} fn caller(value: Int) { target(value) }\n",
    )

    diagnostics = latest_diagnostics(server, uri)
    mismatch = [item for item in diagnostics if item["code"] == "nova.argument-count"]
    assert len(mismatch) == 1
    assert mismatch[0]["message"] == "function 'target' expects 2 argument(s) but got 1"


def test_cross_file_call_tracks_exact_provider_parameter_count() -> None:
    server = initialized_server()
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: Int) {}\n")
    server.drain_notifications()
    open_nova(server, caller, "fn caller(value: Int) { target(value, value) }\n")
    assert latest_codes(server, caller) == ["nova.argument-count"]

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 2},
                "contentChanges": [{"text": "fn target(left: Int, right: Int) {}\n"}],
            },
        )
    )
    assert latest_codes(server, caller) == []


def test_nested_call_commas_do_not_inflate_outer_argument_count() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn pair(left: Int, right: Int) {} "
        "fn target(first: Int, second: Int) {} "
        "fn caller(a: Int, b: Int, c: Int) { target(pair(a, b), c) }\n"
    )
    open_nova(server, uri, text)

    assert latest_codes(server, uri) == []


def test_ambiguous_function_keeps_resolution_diagnostic_without_arity_guess() -> None:
    server = initialized_server()
    caller = "file:///workspace/caller.nova"
    open_nova(server, "file:///workspace/a.nova", "fn target(value: Int) {}\n")
    server.drain_notifications()
    open_nova(
        server,
        "file:///workspace/b.nova",
        "fn target(left: Int, right: Int) {}\n",
    )
    server.drain_notifications()
    open_nova(server, caller, "fn caller(value: Int) { target(value) }\n")

    assert latest_codes(server, caller) == ["nova.ambiguous-function"]


def test_close_and_reopen_recompute_argument_count_from_current_provider() -> None:
    server = initialized_server()
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: Int) {}\n")
    server.drain_notifications()
    open_nova(server, caller, "fn caller(value: Int) { target(value, value) }\n")
    assert latest_codes(server, caller) == ["nova.argument-count"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": library}}))
    assert latest_codes(server, caller) == ["nova.unresolved-function"]

    open_nova(server, library, "fn target(left: Int, right: Int) {}\n")
    assert latest_codes(server, caller) == []


def test_same_version_workspace_replacement_suppresses_stale_argument_count() -> None:
    server = initialized_server()
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: Int) {}\n")
    server.drain_notifications()
    open_nova(server, caller, "fn caller(value: Int) { target(value, value) }\n")
    server.drain_notifications()
    original = server.workspace_symbols.get(caller)
    assert original is not None

    real_commit = server.workspace_symbols.commit_snapshots_if_current
    replaced = False

    def replace_then_commit(snapshots, callback):
        nonlocal replaced
        if not replaced:
            replaced = True
            document = server.documents.get(caller)
            assert document is not None
            replacement = server.nova_adapter.publish(server, document)
            server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    server._publish_workspace_diagnostics()
    notifications = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "textDocument/publishDiagnostics"
        and item.get("params", {}).get("uri") == caller
    ]
    assert notifications
    assert all(
        diagnostic["code"] != "nova.argument-count"
        for item in notifications
        for diagnostic in item["params"]["diagnostics"]
    )
