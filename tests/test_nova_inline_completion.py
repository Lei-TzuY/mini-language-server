from __future__ import annotations

from threading import Event, Thread
from typing import Any

import pytest

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
    inline: bool = True,
    position_encoding: str | None = None,
) -> dict[str, Any]:
    text_document: dict[str, Any] = {}
    if inline:
        text_document["inlineCompletion"] = {}
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


def inline_completion(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    *,
    line: int,
    character: int,
    trigger_kind: int = 1,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/inlineCompletion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "context": {"triggerKind": trigger_kind},
            },
        )
    )
    assert response is not None
    return response


def test_inline_completion_is_negotiated_as_lsp_318_capability() -> None:
    server = NovaProductLanguageServer()
    response = initialize(server)

    assert response["result"]["capabilities"]["inlineCompletionProvider"] == {}


def test_inline_completion_is_not_advertised_or_routed_without_support() -> None:
    server = NovaProductLanguageServer()
    response = initialize(server, inline=False)
    assert "inlineCompletionProvider" not in response["result"]["capabilities"]

    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Unit { tarfoo; }\n")
    rejected = inline_completion(server, uri, 2, line=0, character=23)

    assert rejected["error"]["code"] == -32601


def test_inline_completion_reuses_visible_typed_parameter_candidates() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn main(value: Int) -> Unit { valfoo; }\n"
    open_nova(server, uri, text)

    response = inline_completion(server, uri, 2, line=0, character=34)

    assert response["result"] == [
        {
            "insertText": "value",
            "range": {
                "start": {"line": 0, "character": 31},
                "end": {"line": 0, "character": 37},
            },
        }
    ]


def test_invoked_inline_completion_returns_deterministic_matching_functions() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn targetAlpha() -> Unit { return (); }\n"
        "fn targetBeta() -> Unit { return (); }\n"
        "fn main() -> Unit { tarfoo; }\n"
    )
    open_nova(server, uri, text)

    response = inline_completion(server, uri, 2, line=2, character=23)

    assert response["result"] == [
        {
            "insertText": "targetAlpha",
            "range": {
                "start": {"line": 2, "character": 20},
                "end": {"line": 2, "character": 26},
            },
        },
        {
            "insertText": "targetBeta",
            "range": {
                "start": {"line": 2, "character": 20},
                "end": {"line": 2, "character": 26},
            },
        },
    ]


def test_automatic_inline_completion_returns_only_best_candidate() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn targetAlpha() -> Unit { return (); }\n"
        "fn targetBeta() -> Unit { return (); }\n"
        "fn main() -> Unit { tarfoo; }\n"
    )
    open_nova(server, uri, text)

    response = inline_completion(
        server,
        uri,
        2,
        line=2,
        character=23,
        trigger_kind=2,
    )

    assert [item["insertText"] for item in response["result"]] == ["targetAlpha"]


@pytest.mark.parametrize(
    ("encoding", "cursor", "start", "end"),
    [
        ("utf-8", 13, 10, 16),
        ("utf-16", 11, 8, 14),
        ("utf-32", 10, 7, 13),
    ],
)
def test_inline_completion_range_uses_negotiated_position_units(
    encoding: str,
    cursor: int,
    start: int,
    end: int,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server, position_encoding=encoding)
    uri = "file:///workspace/main.nova"
    text = (
        "fn target() -> Unit { return (); }\n"
        "fn main() -> Unit {\n"
        '  "😀"; tarfoo;\n'
        "}\n"
    )
    open_nova(server, uri, text)

    response = inline_completion(
        server,
        uri,
        2,
        line=2,
        character=cursor,
        trigger_kind=2,
    )

    assert response["result"] == [
        {
            "insertText": "target",
            "range": {
                "start": {"line": 2, "character": start},
                "end": {"line": 2, "character": end},
            },
        }
    ]


def test_inline_completion_validates_required_trigger_context() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Unit { tarfoo; }\n")

    response = server.handle(
        request(
            "textDocument/inlineCompletion",
            2,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 23},
                "context": {"triggerKind": 3},
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32602, "message": "Invalid params"},
    }


def test_inline_completion_fails_closed_for_member_and_conversion_contexts() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    member_uri = "file:///workspace/member.nova"
    conversion_uri = "file:///workspace/conversion.nova"
    open_nova(server, member_uri, "fn main() -> Unit { UInt::Mfoo; }\n")
    open_nova(
        server,
        conversion_uri,
        "fn main(value: Int) -> Unit { UInt::from(vafoo); }\n",
    )

    member = inline_completion(server, member_uri, 2, line=0, character=30)
    conversion = inline_completion(server, conversion_uri, 3, line=0, character=44)

    assert member["result"] == []
    assert conversion["result"] == []


def test_inline_completion_cancellation_uses_its_own_request_generation(
    monkeypatch,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn target() -> Unit { return (); } fn main() { tarfoo; }\n")

    entered = Event()
    release = Event()
    original = server._typed_completion_items

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(server, "_typed_completion_items", blocked)
    responses: list[dict[str, Any] | None] = []
    thread = Thread(
        target=lambda: responses.append(
            inline_completion(server, uri, 2, line=0, character=52)
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


def test_cross_file_workspace_drift_rejects_inline_completion(monkeypatch) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn target() -> Unit { return (); }\n")
    main_text = "fn main() -> Unit { tarfoo; }\n"
    open_nova(server, main_uri, main_text)

    entered = Event()
    release = Event()
    original = server.workspace_symbols.commit_snapshots_if_current

    def blocked(snapshots, commit):
        entered.set()
        assert release.wait(timeout=5)
        return original(snapshots, commit)

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        blocked,
    )
    responses: list[dict[str, Any] | None] = []
    thread = Thread(
        target=lambda: responses.append(
            inline_completion(server, main_uri, 2, line=0, character=23)
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [
                    {"text": "fn renamed() -> Unit { return (); }\n"}
                ],
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
