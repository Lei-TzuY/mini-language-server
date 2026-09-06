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


def test_typed_parameter_forwarding_reports_exact_argument_type_mismatch() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn target(value: Int) {} fn caller(value: String) { target(value) }\n"
    open_nova(server, uri, text)

    diagnostics = latest_diagnostics(server, uri)
    mismatches = [item for item in diagnostics if item["code"] == "nova.argument-type"]
    assert len(mismatches) == 1
    assert mismatches[0]["message"] == (
        "argument 1 to 'target' has type 'String'; expected 'Int'"
    )
    argument_start = text.rindex("value")
    assert mismatches[0]["range"]["start"] == {
        "line": 0,
        "character": argument_start,
    }
    assert mismatches[0]["range"]["end"] == {
        "line": 0,
        "character": argument_start + len("value"),
    }


def test_matching_or_untyped_parameter_arguments_are_not_guessed() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn target(value: Int) {} "
            "fn matching(value: Int) { target(value) } "
            "fn unknown(value) { target(value) }\n"
        ),
    )

    assert latest_codes(server, uri) == []


def test_cross_file_expected_signature_checks_forwarded_parameter_type() -> None:
    server = initialized_server()
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: Int) {}\n")
    server.drain_notifications()
    open_nova(server, caller, "fn caller(value: String) { target(value) }\n")
    assert latest_codes(server, caller) == ["nova.argument-type"]

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 2},
                "contentChanges": [{"text": "fn target(value: String) {}\n"}],
            },
        )
    )
    assert latest_codes(server, caller) == []


def test_caller_parameter_change_recomputes_forwarded_argument_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn target(value: Int) {} fn caller(value: String) { target(value) }\n",
    )
    assert latest_codes(server, uri) == ["nova.argument-type"]

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "text": (
                            "fn target(value: Int) {} "
                            "fn caller(value: Int) { target(value) }\n"
                        )
                    }
                ],
            },
        )
    )
    assert latest_codes(server, uri) == []


def test_close_and_reopen_rebinds_forwarded_parameter_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn target(value: Int) {} fn caller(value: String) { target(value) }\n",
    )
    assert latest_codes(server, uri) == ["nova.argument-type"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(
        server,
        uri,
        "fn target(value: Int) {} fn caller(value: Int) { target(value) }\n",
    )
    assert latest_codes(server, uri) == []


def test_same_version_replacement_suppresses_stale_forwarded_parameter_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn target(value: Int) {} fn caller(value: String) { target(value) }\n",
    )
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
    notifications = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "textDocument/publishDiagnostics"
        and item.get("params", {}).get("uri") == uri
    ]
    assert notifications
    assert all(
        diagnostic["code"] != "nova.argument-type"
        for item in notifications
        for diagnostic in item["params"]["diagnostics"]
    )
