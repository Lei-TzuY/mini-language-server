from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int = 1, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize(server: NovaProductLanguageServer, *, supported: bool = True) -> dict:
    text_document = {"onTypeFormatting": {}} if supported else {}
    response = server.handle(
        request("initialize", params={"capabilities": {"textDocument": text_document}})
    )
    assert response is not None
    return response


def open_document(server: NovaProductLanguageServer, uri: str, text: str, version: int = 1) -> None:
    server.handle(
        notification(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": "nova", "version": version, "text": text}},
        )
    )


def on_type(server: NovaProductLanguageServer, uri: str, line: int, character: int, *, request_id: int = 2, ch: str = "}"):
    return server.handle(
        request(
            "textDocument/onTypeFormatting",
            request_id=request_id,
            params={
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "ch": ch,
                "options": {"tabSize": 2, "insertSpaces": True},
            },
        )
    )


def test_on_type_formatting_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    capabilities = initialize(supported)["result"]["capabilities"]
    assert capabilities["documentOnTypeFormattingProvider"] == {
        "firstTriggerCharacter": "}",
        "moreTriggerCharacter": ["\n"],
    }

    unsupported = NovaProductLanguageServer()
    capabilities = initialize(unsupported, supported=False)["result"]["capabilities"]
    assert "documentOnTypeFormattingProvider" not in capabilities


def test_on_type_formatting_reindents_only_current_line_and_ignores_trivia_braces() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn main() {\n  let text = \"}\"\n  // }\n      }\n"
    open_document(server, uri, text)

    assert on_type(server, uri, 3, 7) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": [
            {
                "range": {
                    "start": {"line": 3, "character": 0},
                    "end": {"line": 3, "character": 6},
                },
                "newText": "",
            }
        ],
    }


def test_on_type_formatting_tracks_change_and_close_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn old() {\n      }\n")
    assert on_type(server, uri, 1, 7)["result"][0]["newText"] == ""

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(server, uri, "fn reopened() {\n    let value: Int = 1\n      }\n", version=1)
    assert on_type(server, uri, 2, 7)["result"][0]["newText"] == ""

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn reopened() {\nlet value: Int = 1\n}\n"}],
            },
        )
    )
    assert on_type(server, uri, 2, 1)["result"] == []


def test_on_type_formatting_rejects_same_version_semantic_replacement(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current() {\n      }\n")
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_on_second_checkpoint(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 2:
            document = server.documents.get(uri)
            assert document is not None
            server.nova_adapter.publish(server, document)

    monkeypatch.setattr(server.requests, "checkpoint", replace_on_second_checkpoint)
    assert on_type(server, uri, 1, 7) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_on_type_formatting_honors_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current() {\n      }\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)
    assert on_type(server, uri, 1, 7) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
