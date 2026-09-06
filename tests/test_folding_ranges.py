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
    text_document = {"foldingRange": {}} if supported else {}
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


def folding_ranges(server: NovaProductLanguageServer, uri: str, request_id: int = 2):
    return server.handle(
        request(
            "textDocument/foldingRange",
            request_id=request_id,
            params={"textDocument": {"uri": uri}},
        )
    )


def test_folding_range_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    assert initialize(supported)["result"]["capabilities"]["foldingRangeProvider"] is True

    unsupported = NovaProductLanguageServer()
    response = initialize(unsupported, supported=False)
    assert "foldingRangeProvider" not in response["result"]["capabilities"]


def test_folding_ranges_cover_complete_multiline_nova_functions_deterministically() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(
        server,
        uri,
        (
            "fn first() {\n  let value = 1\n}\n"
            "fn inline() {}\n"
            "fn second() {\n  first()\n  inline()\n}\n"
        ),
    )

    assert folding_ranges(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": [
            {"startLine": 0, "endLine": 1},
            {"startLine": 4, "endLine": 6},
        ],
    }


def test_folding_ranges_follow_did_change_and_close_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn old() {\n  let value = 1\n}\n")
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn new() {}\n"}],
            },
        )
    )
    assert folding_ranges(server, uri)["result"] == []

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(server, uri, "fn reopened() {\n  let value = true\n}\n", version=1)
    assert folding_ranges(server, uri)["result"] == [{"startLine": 0, "endLine": 1}]


def test_folding_ranges_reject_same_version_semantic_replacement(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current() {\n  let value = 1\n}\n")
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
    assert folding_ranges(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_folding_ranges_honor_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current() {\n  let value = 1\n}\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)
    assert folding_ranges(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
