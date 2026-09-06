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


def test_literal_initialized_local_reports_argument_type_mismatch() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn target(value: Int) {} fn caller() { let value = "text" target(value) }\n'
    open_nova(server, uri, text)
    assert latest_codes(server, uri) == ["nova.argument-type"]


def test_matching_and_nonliteral_local_initializers_are_bounded() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn target(value: Int) {} fn caller(source: String) { "
        "let good = 1 target(good) let unknown = source target(unknown) }\n"
    )
    open_nova(server, uri, text)
    assert latest_codes(server, uri) == []


def test_local_initializer_change_recomputes_argument_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn target(value: Int) {} fn caller() { let value = "text" target(value) }\n'
    open_nova(server, uri, text)
    assert latest_codes(server, uri) == ["nova.argument-type"]

    changed_text = "fn target(value: Int) {} fn caller() { let value = 1 target(value) }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": changed_text}],
            },
        )
    )
    assert latest_codes(server, uri) == []


def test_close_reopen_rebinds_local_initializer_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn target(value: Int) {} fn caller() { let value = "text" target(value) }\n'
    open_nova(server, uri, text)
    assert latest_codes(server, uri) == ["nova.argument-type"]
    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    reopened_text = "fn target(value: Int) {} fn caller() { let value = 1 target(value) }\n"
    open_nova(server, uri, reopened_text)
    assert latest_codes(server, uri) == []
