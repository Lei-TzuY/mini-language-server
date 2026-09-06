from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server(*, signature_help: bool = False) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    text_document = {"signatureHelp": {}} if signature_help else {}
    result = server.handle(
        request("initialize", 1, {"capabilities": {"textDocument": text_document}})
    )
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


def test_string_delimiters_do_not_corrupt_arity_or_literal_types() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn target(text: String, flag: Bool) {} '
        'fn caller() { target("a,(b),\\\"c", false) }\n'
    )
    open_nova(server, uri, text)

    assert latest_codes(server, uri) == []


def test_string_and_bool_literal_mismatches_use_exact_argument_spans() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn target(text: String, flag: Bool) {} '
        'fn caller() { target(true, "value") }\n'
    )
    open_nova(server, uri, text)

    diagnostics = [
        item
        for item in latest_diagnostics(server, uri)
        if item["code"] == "nova.argument-type"
    ]
    assert [item["message"] for item in diagnostics] == [
        "argument 1 to 'target' has type 'Bool'; expected 'String'",
        "argument 2 to 'target' has type 'String'; expected 'Bool'",
    ]
    first = text.index("true")
    second = text.index('"value"')
    assert diagnostics[0]["range"] == {
        "start": {"line": 0, "character": first},
        "end": {"line": 0, "character": first + len("true")},
    }
    assert diagnostics[1]["range"] == {
        "start": {"line": 0, "character": second},
        "end": {"line": 0, "character": second + len('"value"')},
    }


def test_signature_help_ignores_commas_inside_string_literals() -> None:
    server = initialized_server(signature_help=True)
    uri = "file:///workspace/main.nova"
    text = (
        'fn target(text: String, flag: Bool) {} '
        'fn caller() { target("left,right", false) }\n'
    )
    open_nova(server, uri, text)

    result = server.handle(
        request(
            "textDocument/signatureHelp",
            2,
            {
                "textDocument": {"uri": uri},
                "position": {
                    "line": 0,
                    "character": text.index("false") + 2,
                },
            },
        )
    )
    assert result is not None
    assert result["result"] is not None
    assert result["result"]["activeParameter"] == 1


def test_cross_file_bool_type_tracks_current_provider_signature() -> None:
    server = initialized_server()
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: String) {}\n")
    server.drain_notifications()
    open_nova(server, caller, "fn caller() { target(true) }\n")
    assert latest_codes(server, caller) == ["nova.argument-type"]

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 2},
                "contentChanges": [{"text": "fn target(value: Bool) {}\n"}],
            },
        )
    )
    assert latest_codes(server, caller) == []
