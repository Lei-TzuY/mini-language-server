from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(
    method: str,
    request_id: int,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    *,
    inline_value: bool = True,
    position_encoding: str | None = None,
) -> dict[str, Any]:
    text_document: dict[str, Any] = {}
    if inline_value:
        text_document["inlineValue"] = {}
    capabilities: dict[str, Any] = {"textDocument": text_document}
    if position_encoding is not None:
        capabilities["general"] = {"positionEncodings": [position_encoding]}
    response = server.handle(
        request("initialize", 1, {"capabilities": capabilities})
    )
    assert response is not None
    return response


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    version: int = 1,
) -> None:
    server.handle(
        notify(
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


def inline_values(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    *,
    viewport: tuple[tuple[int, int], tuple[int, int]],
    stopped: tuple[tuple[int, int], tuple[int, int]],
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/inlineValue",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {
                        "line": viewport[0][0],
                        "character": viewport[0][1],
                    },
                    "end": {
                        "line": viewport[1][0],
                        "character": viewport[1][1],
                    },
                },
                "context": {
                    "frameId": 7,
                    "stoppedLocation": {
                        "start": {
                            "line": stopped[0][0],
                            "character": stopped[0][1],
                        },
                        "end": {
                            "line": stopped[1][0],
                            "character": stopped[1][1],
                        },
                    },
                },
            },
        )
    )
    assert response is not None
    return response


def test_inline_value_is_negotiated() -> None:
    server = NovaProductLanguageServer()
    response = initialize(server)

    assert response["result"]["capabilities"]["inlineValueProvider"] is True


def test_inline_value_is_not_advertised_or_routed_without_support() -> None:
    server = NovaProductLanguageServer()
    response = initialize(server, inline_value=False)
    assert "inlineValueProvider" not in response["result"]["capabilities"]

    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main(value) { value; }")
    rejected = inline_values(
        server,
        uri,
        2,
        viewport=((0, 0), (0, 24)),
        stopped=((0, 17), (0, 17)),
    )

    assert rejected["error"]["code"] == -32601


def test_inline_value_returns_visible_parameter_and_local_occurrences() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(input) {\n"
        "  input;\n"
        "  let value = input;\n"
        "  value;\n"
        "}"
    )
    open_nova(server, uri, text)

    response = inline_values(
        server,
        uri,
        2,
        viewport=((0, 0), (4, 1)),
        stopped=((3, 2), (3, 2)),
    )

    assert [
        (
            item["variableName"],
            item["range"]["start"],
            item["range"]["end"],
            item["caseSensitiveLookup"],
        )
        for item in response["result"]
    ] == [
        ("input", {"line": 0, "character": 8}, {"line": 0, "character": 13}, True),
        ("input", {"line": 1, "character": 2}, {"line": 1, "character": 7}, True),
        ("value", {"line": 2, "character": 6}, {"line": 2, "character": 11}, True),
        ("input", {"line": 2, "character": 14}, {"line": 2, "character": 19}, True),
        ("value", {"line": 3, "character": 2}, {"line": 3, "character": 7}, True),
    ]


def test_inline_value_local_shadows_parameter_at_stop_location() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(value) {\n"
        "  value;\n"
        "  let value = 1;\n"
        "  value;\n"
        "}"
    )
    open_nova(server, uri, text)

    response = inline_values(
        server,
        uri,
        2,
        viewport=((0, 0), (4, 1)),
        stopped=((3, 2), (3, 2)),
    )

    assert [
        (
            item["variableName"],
            item["range"]["start"]["line"],
            item["range"]["start"]["character"],
        )
        for item in response["result"]
    ] == [
        ("value", 2, 6),
        ("value", 3, 2),
    ]


def test_inline_value_stop_before_local_keeps_parameter_visible() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(value) {\n"
        "  value;\n"
        "  let value = 1;\n"
        "  value;\n"
        "}"
    )
    open_nova(server, uri, text)

    response = inline_values(
        server,
        uri,
        2,
        viewport=((0, 0), (4, 1)),
        stopped=((1, 2), (1, 2)),
    )

    assert [
        (
            item["variableName"],
            item["range"]["start"]["line"],
            item["range"]["start"]["character"],
        )
        for item in response["result"]
    ] == [
        ("value", 0, 8),
        ("value", 1, 2),
    ]


def test_inline_value_ambiguous_preceding_locals_fail_closed() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn main(value) { let value = 1; let value = 2; value; }"
    open_nova(server, uri, text)

    response = inline_values(
        server,
        uri,
        2,
        viewport=((0, 0), (0, len(text))),
        stopped=((0, text.index("value;", 35)), (0, text.index("value;", 35))),
    )

    assert response["result"] == []


def test_inline_value_viewport_clips_occurrences_without_leaking_other_functions() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn first(alpha) { alpha; }\n"
        "fn second(beta) {\n"
        "  beta;\n"
        "  beta;\n"
        "}"
    )
    open_nova(server, uri, text)

    response = inline_values(
        server,
        uri,
        2,
        viewport=((3, 0), (3, 7)),
        stopped=((2, 2), (2, 2)),
    )

    assert response["result"] == [
        {
            "range": {
                "start": {"line": 3, "character": 2},
                "end": {"line": 3, "character": 6},
            },
            "variableName": "beta",
            "caseSensitiveLookup": True,
        }
    ]


def test_inline_value_uses_negotiated_utf8_ranges() -> None:
    server = NovaProductLanguageServer()
    initialize(server, position_encoding="utf-8")
    uri = "file:///workspace/main.nova"
    text = 'fn main(value) { "😀"; value; }'
    open_nova(server, uri, text)

    response = inline_values(
        server,
        uri,
        2,
        viewport=((0, 0), (0, len(text.encode()))),
        stopped=((0, len('fn main(value) { "😀"; '.encode())),) * 2,
    )

    assert response["result"][-1] == {
        "range": {
            "start": {
                "line": 0,
                "character": len('fn main(value) { "😀"; '.encode()),
            },
            "end": {
                "line": 0,
                "character": len('fn main(value) { "😀"; value'.encode()),
            },
        },
        "variableName": "value",
        "caseSensitiveLookup": True,
    }


def test_inline_value_rejects_invalid_debug_context() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main(value) { value; }")

    response = server.handle(
        request(
            "textDocument/inlineValue",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 24},
                },
                "context": {
                    "frameId": True,
                    "stoppedLocation": {
                        "start": {"line": 0, "character": 17},
                        "end": {"line": 0, "character": 17},
                    },
                },
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32602, "message": "Invalid params"},
    }


def test_inline_value_cancellation_uses_request_generation(monkeypatch) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn main(value) { value; }"
    open_nova(server, uri, text)

    entered = Event()
    release = Event()
    original = server._inline_value_visible_targets

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(server, "_inline_value_visible_targets", blocked)
    responses: list[dict[str, Any] | None] = []
    thread = Thread(
        target=lambda: responses.append(
            inline_values(
                server,
                uri,
                2,
                viewport=((0, 0), (0, len(text))),
                stopped=((0, 17), (0, 17)),
            )
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    server.handle(notify("$/cancelRequest", {"id": 2}))
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
    assert len(server.requests) == 0


def test_inline_value_same_document_drift_is_rejected(monkeypatch) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn main(value) { value; }"
    open_nova(server, uri, text)

    entered = Event()
    release = Event()
    original = server._inline_value_visible_targets

    def blocked(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(server, "_inline_value_visible_targets", blocked)
    responses: list[dict[str, Any] | None] = []
    thread = Thread(
        target=lambda: responses.append(
            inline_values(
                server,
                uri,
                2,
                viewport=((0, 0), (0, len(text))),
                stopped=((0, 17), (0, 17)),
            )
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main(other) { other; }"}],
            },
        )
    )
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32801, "message": "Content modified"},
        }
    ]
