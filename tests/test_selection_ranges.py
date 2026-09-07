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
    text_document = {"selectionRange": {}} if supported else {}
    response = server.handle(
        request("initialize", params={"capabilities": {"textDocument": text_document}})
    )
    assert response is not None
    return response


def open_document(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
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


def selection_ranges(
    server: NovaProductLanguageServer,
    uri: str,
    positions: list[dict[str, int]],
    request_id: int = 2,
):
    return server.handle(
        request(
            "textDocument/selectionRange",
            request_id=request_id,
            params={"textDocument": {"uri": uri}, "positions": positions},
        )
    )


def test_selection_range_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    assert initialize(supported)["result"]["capabilities"]["selectionRangeProvider"] is True

    unsupported = NovaProductLanguageServer()
    response = initialize(unsupported, supported=False)
    assert "selectionRangeProvider" not in response["result"]["capabilities"]


def test_selection_ranges_expand_token_to_function_to_document() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn greet(name: String) {\n"
        "  let alias = name\n"
        "  print(alias)\n"
        "}\n"
        "outside\n"
    )
    open_document(server, uri, text)

    response = selection_ranges(
        server,
        uri,
        [
            {"line": 0, "character": 4},
            {"line": 1, "character": 7},
            {"line": 4, "character": 2},
        ],
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": [
            {
                "range": {
                    "start": {"line": 0, "character": 3},
                    "end": {"line": 0, "character": 8},
                },
                "parent": {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 3, "character": 1},
                    },
                    "parent": {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 5, "character": 0},
                        }
                    },
                },
            },
            {
                "range": {
                    "start": {"line": 1, "character": 6},
                    "end": {"line": 1, "character": 11},
                },
                "parent": {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 3, "character": 1},
                    },
                    "parent": {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 5, "character": 0},
                        }
                    },
                },
            },
            {
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 5, "character": 0},
                }
            },
        ],
    }


def test_selection_ranges_follow_did_change_and_close_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn old() {\n  old()\n}\n")

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn newer() {\n  newer()\n}\n"}],
            },
        )
    )
    changed = selection_ranges(server, uri, [{"line": 1, "character": 3}])
    assert changed is not None
    assert changed["result"][0]["range"] == {
        "start": {"line": 1, "character": 2},
        "end": {"line": 1, "character": 7},
    }

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(server, uri, "fn reopened() {\n  reopened()\n}\n", version=1)
    reopened = selection_ranges(server, uri, [{"line": 0, "character": 4}])
    assert reopened is not None
    assert reopened["result"][0]["range"] == {
        "start": {"line": 0, "character": 3},
        "end": {"line": 0, "character": 11},
    }


def test_selection_ranges_reject_same_version_semantic_replacement(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current() {\n  current()\n}\n")
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
    assert selection_ranges(server, uri, [{"line": 1, "character": 3}]) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_selection_ranges_honor_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current() {\n  current()\n}\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)
    assert selection_ranges(server, uri, [{"line": 1, "character": 3}]) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
