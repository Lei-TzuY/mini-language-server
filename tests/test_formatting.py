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
    text_document = {"formatting": {}} if supported else {}
    response = server.handle(
        request("initialize", params={"capabilities": {"textDocument": text_document}})
    )
    assert response is not None
    return response


def open_document(
    server: NovaProductLanguageServer, uri: str, text: str, version: int = 1
) -> None:
    server.handle(
        notification(
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


def formatting(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    request_id: int = 2,
    tab_size: int = 2,
    insert_spaces: bool = True,
):
    return server.handle(
        request(
            "textDocument/formatting",
            request_id=request_id,
            params={
                "textDocument": {"uri": uri},
                "options": {"tabSize": tab_size, "insertSpaces": insert_spaces},
            },
        )
    )


def test_document_formatting_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    assert (
        initialize(supported)["result"]["capabilities"]["documentFormattingProvider"]
        is True
    )

    unsupported = NovaProductLanguageServer()
    response = initialize(unsupported, supported=False)
    assert "documentFormattingProvider" not in response["result"]["capabilities"]


def test_document_formatting_is_deterministic_and_trivia_aware() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() {\n"
        "let text = \"}\"\n"
        "// } does not close the function\n"
        "helper()\n"
        "}\n"
        "fn helper() {\n"
        "/* { does not open a scope */\n"
        "}\n"
    )
    open_document(server, uri, text)

    expected = (
        "fn main() {\n"
        "  let text = \"}\"\n"
        "  // } does not close the function\n"
        "  helper()\n"
        "}\n"
        "fn helper() {\n"
        "  /* { does not open a scope */\n"
        "}\n"
    )
    response = formatting(server, uri)
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": [
            {
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 8, "character": 0},
                },
                "newText": expected,
            }
        ],
    }

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": expected}],
            },
        )
    )
    assert formatting(server, uri)["result"] == []


def test_document_formatting_follows_close_reopen_and_tab_options() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn old() {\nlet value = 1\n}\n")
    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(server, uri, "fn reopened() {\nlet value = true\n}\n", version=1)

    response = formatting(server, uri, insert_spaces=False)
    assert response is not None
    assert response["result"][0]["newText"] == "fn reopened() {\n\tlet value = true\n}\n"


def test_document_formatting_rejects_same_version_semantic_replacement(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current() {\nlet value = 1\n}\n")
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
    assert formatting(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_document_formatting_honors_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current() {\nlet value = 1\n}\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)
    assert formatting(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
