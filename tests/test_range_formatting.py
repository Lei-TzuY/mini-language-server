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
    text_document = {"rangeFormatting": {}} if supported else {}
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


def range_formatting(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    start: tuple[int, int],
    end: tuple[int, int],
    request_id: int = 2,
    tab_size: int = 2,
    insert_spaces: bool = True,
):
    return server.handle(
        request(
            "textDocument/rangeFormatting",
            request_id=request_id,
            params={
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": start[0], "character": start[1]},
                    "end": {"line": end[0], "character": end[1]},
                },
                "options": {"tabSize": tab_size, "insertSpaces": insert_spaces},
            },
        )
    )


def test_range_formatting_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    assert (
        initialize(supported)["result"]["capabilities"][
            "documentRangeFormattingProvider"
        ]
        is True
    )

    unsupported = NovaProductLanguageServer()
    response = initialize(unsupported, supported=False)
    assert "documentRangeFormattingProvider" not in response["result"]["capabilities"]


def test_range_formatting_only_edits_contained_lines_and_is_trivia_aware() -> None:
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

    response = range_formatting(server, uri, start=(1, 0), end=(4, 0))
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": [
            {
                "range": {
                    "start": {"line": 1, "character": 0},
                    "end": {"line": 1, "character": 0},
                },
                "newText": "  ",
            },
            {
                "range": {
                    "start": {"line": 2, "character": 0},
                    "end": {"line": 2, "character": 0},
                },
                "newText": "  ",
            },
            {
                "range": {
                    "start": {"line": 3, "character": 0},
                    "end": {"line": 3, "character": 0},
                },
                "newText": "  ",
            },
        ],
    }


def test_range_formatting_does_not_escape_partial_boundary_lines() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\nlet a = 1\nlet b = 2\n}\n")

    response = range_formatting(server, uri, start=(1, 2), end=(2, 2))
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": [
            {
                "range": {
                    "start": {"line": 2, "character": 0},
                    "end": {"line": 2, "character": 0},
                },
                "newText": "  ",
            }
        ],
    }


def test_range_formatting_tracks_change_close_reopen_and_tab_options() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn old() {\nlet value = 1\n}\n")
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn changed() {\nlet value = true\n}\n"}],
            },
        )
    )
    response = range_formatting(
        server,
        uri,
        start=(1, 0),
        end=(1, 16),
        insert_spaces=False,
    )
    assert response is not None
    assert response["result"] == [
        {
            "range": {
                "start": {"line": 1, "character": 0},
                "end": {"line": 1, "character": 0},
            },
            "newText": "\t",
        }
    ]

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(server, uri, "fn reopened() {\n  let value = 1\n}\n", version=1)
    assert (
        range_formatting(server, uri, start=(1, 0), end=(1, 15))["result"] == []
    )


def test_range_formatting_rejects_same_version_semantic_replacement(
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
    assert range_formatting(server, uri, start=(1, 0), end=(1, 13)) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_range_formatting_honors_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current() {\nlet value = 1\n}\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)
    assert range_formatting(server, uri, start=(1, 0), end=(1, 13)) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
