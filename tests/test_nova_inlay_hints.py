from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer, *, supported: bool = True) -> dict:
    text_document = {"inlayHint": {}} if supported else {}
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": text_document}},
        )
    )
    assert result is not None
    return result


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


def inlay_hints(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    request_id: int = 2,
    start_character: int = 0,
    end_character: int | None = None,
) -> dict:
    if end_character is None:
        end_character = len(text.rstrip("\n"))
    result = server.handle(
        request(
            "textDocument/inlayHint",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": start_character},
                    "end": {"line": 0, "character": end_character},
                },
            },
        )
    )
    assert result is not None
    return result


def test_inlay_hints_are_negotiated() -> None:
    supported = NovaProductLanguageServer()
    capabilities = initialize(supported)["result"]["capabilities"]
    assert capabilities["inlayHintProvider"] is True

    unsupported = NovaProductLanguageServer()
    capabilities = initialize(unsupported, supported=False)["result"]["capabilities"]
    assert "inlayHintProvider" not in capabilities


def test_inlay_hints_use_exact_cross_file_parameter_names() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(
        server,
        library_uri,
        "fn target(value: Int, flag: Bool) -> String { value }\n",
    )
    text = "fn caller() { target(1, true); }\n"
    open_nova(server, main_uri, text)

    assert inlay_hints(server, main_uri, text)["result"] == [
        {
            "position": {"line": 0, "character": text.index("1")},
            "label": "value:",
            "kind": 2,
            "paddingRight": True,
        },
        {
            "position": {"line": 0, "character": text.index("true")},
            "label": "flag:",
            "kind": 2,
            "paddingRight": True,
        },
    ]


def test_inlay_hints_track_nested_calls_and_requested_range() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn target(first: Int, second: Int) -> Int { first } "
        "fn nested(left: Int, right: Int) -> Int { left } "
        "fn caller() { target(nested(1, 2), 3) }\n"
    )
    open_nova(server, uri, text)

    start = text.rindex("nested")
    end = text.rindex("3") + 1
    result = inlay_hints(
        server,
        uri,
        text,
        start_character=start,
        end_character=end,
    )["result"]
    assert [(item["position"]["character"], item["label"]) for item in result] == [
        (text.rindex("nested"), "first:"),
        (text.rindex("1"), "left:"),
        (text.rindex("2"), "right:"),
        (text.rindex("3"), "second:"),
    ]


def test_inlay_hints_do_not_guess_ambiguous_function() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/a.nova", "fn target(value: Int) {}\n")
    open_nova(server, "file:///workspace/b.nova", "fn target(flag: Bool) {}\n")
    text = "fn caller() { target(1) }\n"
    open_nova(server, main_uri, text)

    assert inlay_hints(server, main_uri, text)["result"] == []


def test_inlay_hints_track_change_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    text = "fn caller() { target(1); }\n"
    open_nova(server, library_uri, "fn target(value: Int) -> Int { value }\n")
    open_nova(server, main_uri, text)
    assert inlay_hints(server, main_uri, text)["result"][0]["label"] == "value:"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library_uri, "version": 2},
                "contentChanges": [
                    {"text": "fn target(renamed: String) -> String { renamed }\n"}
                ],
            },
        )
    )
    assert inlay_hints(server, main_uri, text, request_id=3)["result"][0]["label"] == (
        "renamed:"
    )

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": library_uri}})
    )
    assert inlay_hints(server, main_uri, text, request_id=4)["result"] == []

    open_nova(server, library_uri, "fn target(flag: Bool) -> Bool { flag }\n")
    assert inlay_hints(server, main_uri, text, request_id=5)["result"][0]["label"] == (
        "flag:"
    )


def test_inlay_hints_suppress_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    text = "fn caller() { target(1); }\n"
    open_nova(server, library_uri, "fn target(value: Int) -> Int { value }\n")
    open_nova(server, main_uri, text)
    original = server.workspace_symbols.get(library_uri)
    assert original is not None

    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(library_uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    assert inlay_hints(server, main_uri, text) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_inlay_hints_honor_cancellation_checkpoint() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn target(value: Int) {} fn caller() { target(1); }\n"
    open_nova(server, uri, text)
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert inlay_hints(server, uri, text) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
