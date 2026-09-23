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


def initialize(
    server: NovaProductLanguageServer,
    *,
    supported: bool = True,
    multiple: bool = False,
    position_encoding: str | None = None,
) -> dict:
    range_formatting = {"rangesSupport": True} if multiple else {}
    text_document = {"rangeFormatting": range_formatting} if supported else {}
    capabilities: dict[str, Any] = {"textDocument": text_document}
    if position_encoding is not None:
        capabilities["general"] = {"positionEncodings": [position_encoding]}
    response = server.handle(
        request("initialize", params={"capabilities": capabilities})
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


def ranges_formatting(
    server: NovaProductLanguageServer,
    uri: str,
    ranges: list[tuple[tuple[int, int], tuple[int, int]]],
    *,
    request_id: int = 2,
    tab_size: int = 2,
    insert_spaces: bool = True,
):
    return server.handle(
        request(
            "textDocument/rangesFormatting",
            request_id=request_id,
            params={
                "textDocument": {"uri": uri},
                "ranges": [
                    {
                        "start": {"line": start[0], "character": start[1]},
                        "end": {"line": end[0], "character": end[1]},
                    }
                    for start, end in ranges
                ],
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

    multi = NovaProductLanguageServer()
    assert initialize(multi, multiple=True)["result"]["capabilities"][
        "documentRangeFormattingProvider"
    ] == {"rangesSupport": True}

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
\n
def test_ranges_formatting_formats_disjoint_ranges_in_one_snapshot() -> None:
    server = NovaProductLanguageServer()
    initialize(server, multiple=True)
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() {\n"
        "let first = 1\n"
        "if (true) {\n"
        "let nested = 2\n"
        "}\n"
        "let untouched = 3\n"
        "}\n"
    )
    open_document(server, uri, text)

    response = ranges_formatting(
        server,
        uri,
        [
            ((1, 0), (1, 13)),
            ((3, 0), (3, 14)),
        ],
    )

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
                    "start": {"line": 3, "character": 0},
                    "end": {"line": 3, "character": 0},
                },
                "newText": "    ",
            },
        ],
    }


def test_ranges_formatting_overlaps_do_not_duplicate_edits() -> None:
    server = NovaProductLanguageServer()
    initialize(server, multiple=True)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\nlet value = 1\n}\n")

    response = ranges_formatting(
        server,
        uri,
        [
            ((1, 0), (1, 13)),
            ((0, 0), (2, 1)),
            ((1, 0), (1, 13)),
        ],
    )

    assert response is not None
    assert response["result"] == [
        {
            "range": {
                "start": {"line": 1, "character": 0},
                "end": {"line": 1, "character": 0},
            },
            "newText": "  ",
        }
    ]


def test_ranges_formatting_empty_ranges_is_a_valid_noop() -> None:
    server = NovaProductLanguageServer()
    initialize(server, multiple=True)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\nlet value = 1\n}\n")

    assert ranges_formatting(server, uri, []) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": [],
    }


def test_ranges_formatting_rejects_entire_request_when_one_range_is_invalid() -> None:
    server = NovaProductLanguageServer()
    initialize(server, multiple=True)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\nlet value = 1\n}\n")

    response = server.handle(
        request(
            "textDocument/rangesFormatting",
            2,
            {
                "textDocument": {"uri": uri},
                "ranges": [
                    {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 1, "character": 13},
                    },
                    {
                        "start": {"line": 99, "character": 0},
                        "end": {"line": 99, "character": 1},
                    },
                ],
                "options": {"tabSize": 2, "insertSpaces": True},
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32602, "message": "Invalid params"},
    }


def test_ranges_formatting_uses_negotiated_utf8_positions() -> None:
    server = NovaProductLanguageServer()
    initialize(server, multiple=True, position_encoding="utf-8")
    uri = "file:///workspace/main.nova"
    text = 'fn main() {\nhelper("😀")\n}\n'
    open_document(server, uri, text)

    response = ranges_formatting(
        server,
        uri,
        [((1, 0), (1, 14))],
    )

    assert response is not None
    assert response["result"] == [
        {
            "range": {
                "start": {"line": 1, "character": 0},
                "end": {"line": 1, "character": 0},
            },
            "newText": "  ",
        }
    ]


def test_ranges_formatting_rejects_same_version_semantic_replacement(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server, multiple=True)
    uri = "file:///workspace/main.nova"
    open_document(
        server,
        uri,
        "fn current() {\nlet first = 1\nlet second = 2\n}\n",
    )
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_during_request(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 3:
            document = server.documents.get(uri)
            assert document is not None
            server.nova_adapter.publish(server, document)

    monkeypatch.setattr(server.requests, "checkpoint", replace_during_request)
    assert ranges_formatting(
        server,
        uri,
        [
            ((1, 0), (1, 13)),
            ((2, 0), (2, 14)),
        ],
    ) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_ranges_formatting_honors_cancellation_between_ranges(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server, multiple=True)
    uri = "file:///workspace/main.nova"
    open_document(
        server,
        uri,
        "fn current() {\nlet first = 1\nlet second = 2\n}\n",
    )
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def cancel_during_request(context: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_during_request)
    assert ranges_formatting(
        server,
        uri,
        [
            ((1, 0), (1, 13)),
            ((2, 0), (2, 14)),
        ],
    ) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
