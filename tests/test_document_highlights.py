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
    text_document = {"documentHighlight": {}} if supported else {}
    response = server.handle(
        request(
            "initialize",
            params={"capabilities": {"textDocument": text_document}},
        )
    )
    assert response is not None
    return response


def open_document(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    version: int = 1,
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


def highlights(
    server: NovaProductLanguageServer,
    uri: str,
    line: int,
    character: int,
    *,
    request_id: int = 2,
):
    return server.handle(
        request(
            "textDocument/documentHighlight",
            request_id=request_id,
            params={
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
    )


def test_document_highlight_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    supported_response = initialize(supported)
    assert supported_response["result"]["capabilities"]["documentHighlightProvider"] is True

    unsupported = NovaProductLanguageServer()
    unsupported_response = initialize(unsupported, supported=False)
    assert "documentHighlightProvider" not in unsupported_response["result"]["capabilities"]


def test_document_highlights_classify_declaration_and_references_deterministically() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(
        server,
        uri,
        "fn target(value: Int) {}\nfn main() { target(1) target(2) }\n",
    )

    response = highlights(server, uri, 1, 13)

    assert response is not None
    assert response["result"] == [
        {
            "range": {
                "start": {"line": 0, "character": 3},
                "end": {"line": 0, "character": 9},
            },
            "kind": 3,
        },
        {
            "range": {
                "start": {"line": 1, "character": 12},
                "end": {"line": 1, "character": 18},
            },
            "kind": 2,
        },
        {
            "range": {
                "start": {"line": 1, "character": 22},
                "end": {"line": 1, "character": 28},
            },
            "kind": 2,
        },
    ]


def test_document_highlights_return_empty_without_target() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {}\n")

    assert highlights(server, uri, 0, 0) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": [],
    }


def test_document_highlights_follow_did_change() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn old() {}\nfn main() { old() }\n")

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {"text": "fn fresh() {}\nfn main() { fresh() }\n"}
                ],
            },
        )
    )

    response = highlights(server, uri, 1, 13)
    assert response is not None
    assert response["result"] == [
        {
            "range": {
                "start": {"line": 0, "character": 3},
                "end": {"line": 0, "character": 8},
            },
            "kind": 3,
        },
        {
            "range": {
                "start": {"line": 1, "character": 12},
                "end": {"line": 1, "character": 17},
            },
            "kind": 2,
        },
    ]


def test_document_highlights_follow_close_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn old() {}\nfn main() { old() }\n")
    server.handle(
        notification("textDocument/didClose", {"textDocument": {"uri": uri}})
    )
    open_document(server, uri, "fn fresh() {}\nfn main() { fresh() }\n")

    response = highlights(server, uri, 1, 13)
    assert response is not None
    assert response["result"][0] == {
        "range": {
            "start": {"line": 0, "character": 3},
            "end": {"line": 0, "character": 8},
        },
        "kind": 3,
    }


def test_document_highlights_reject_same_version_semantic_replacement(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn target() {}\nfn main() { target() }\n")
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_on_third_checkpoint(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 3:
            document = server.documents.get(uri)
            assert document is not None
            server.nova_adapter.publish(server, document)

    monkeypatch.setattr(server.requests, "checkpoint", replace_on_third_checkpoint)

    assert highlights(server, uri, 1, 13) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_document_highlights_honor_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn target() {}\nfn main() { target() }\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)

    assert highlights(server, uri, 1, 13) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
