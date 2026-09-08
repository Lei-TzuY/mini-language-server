from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
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


def latest_codes(server: NovaProductLanguageServer, uri: str) -> list[str]:
    notifications = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "textDocument/publishDiagnostics"
        and item.get("params", {}).get("uri") == uri
    ]
    assert notifications
    return [item["code"] for item in notifications[-1]["params"]["diagnostics"]]


def test_same_file_function_call_argument_reports_type_mismatch() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn helper() -> String { return "x"; } '
        "fn target(value: Int) {} fn caller() { target(helper()) }\n",
    )
    assert latest_codes(server, uri) == ["nova.argument-type"]


def test_cross_file_function_call_argument_reports_type_mismatch() -> None:
    server = initialized_server()
    caller = "file:///workspace/caller.nova"
    open_nova(server, "file:///workspace/helper.nova", 'fn helper() -> String { return "x"; }\n')
    open_nova(
        server,
        caller,
        "fn target(value: Int) {} fn caller() { target(helper()) }\n",
    )
    assert latest_codes(server, caller) == ["nova.argument-type"]


def test_matching_function_call_argument_type_is_accepted() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn helper() -> Int { return 1; } "
        "fn target(value: Int) {} fn caller() { target(helper()) }\n",
    )
    assert latest_codes(server, uri) == []


def test_ambiguous_function_call_argument_type_is_not_guessed() -> None:
    server = initialized_server()
    caller = "file:///workspace/caller.nova"
    open_nova(server, "file:///workspace/a.nova", "fn helper() -> Int { return 1; }\n")
    open_nova(server, "file:///workspace/b.nova", 'fn helper() -> String { return "x"; }\n')
    open_nova(
        server,
        caller,
        "fn target(value: Bool) {} fn caller() { target(helper()) }\n",
    )
    assert latest_codes(server, caller) == ["nova.ambiguous-function"]


def test_function_return_type_change_recomputes_argument_diagnostic() -> None:
    server = initialized_server()
    helper = "file:///workspace/helper.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, helper, 'fn helper() -> String { return "x"; }\n')
    open_nova(
        server,
        caller,
        "fn target(value: Int) {} fn caller() { target(helper()) }\n",
    )
    assert latest_codes(server, caller) == ["nova.argument-type"]
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper, "version": 2},
                "contentChanges": [{"text": "fn helper() -> Int { return 1; }\n"}],
            },
        )
    )
    assert latest_codes(server, caller) == []


def test_close_reopen_rebinds_function_call_argument_type() -> None:
    server = initialized_server()
    helper = "file:///workspace/helper.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, helper, 'fn helper() -> String { return "x"; }\n')
    open_nova(
        server,
        caller,
        "fn target(value: Int) {} fn caller() { target(helper()) }\n",
    )
    assert latest_codes(server, caller) == ["nova.argument-type"]
    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": helper}}))
    open_nova(server, helper, "fn helper() -> Int { return 1; }\n")
    assert latest_codes(server, caller) == []
