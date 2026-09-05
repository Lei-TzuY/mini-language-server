from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer, *, supported: bool = True) -> dict:
    text_document = {"signatureHelp": {}} if supported else {}
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


def signature_help(
    server: NovaProductLanguageServer,
    uri: str,
    character: int,
    *,
    request_id: int = 2,
) -> dict:
    result = server.handle(
        request(
            "textDocument/signatureHelp",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": character},
            },
        )
    )
    assert result is not None
    return result


def test_signature_help_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    capabilities = initialize(supported)["result"]["capabilities"]
    assert capabilities["signatureHelpProvider"] == {
        "triggerCharacters": ["(", ","]
    }

    unsupported = NovaProductLanguageServer()
    capabilities = initialize(unsupported, supported=False)["result"]["capabilities"]
    assert "signatureHelpProvider" not in capabilities


def test_signature_help_resolves_exact_cross_file_typed_signature() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(
        server,
        library_uri,
        "fn target(value: Int, flag: Bool) -> String { value }\n",
    )
    text = "fn caller() { target(value, flag) }\n"
    open_nova(server, main_uri, text)

    result = signature_help(server, main_uri, text.index("flag") + 2)["result"]
    assert result == {
        "signatures": [
            {
                "label": "fn target(value: Int, flag: Bool) -> String",
                "parameters": [
                    {"label": "value: Int"},
                    {"label": "flag: Bool"},
                ],
            }
        ],
        "activeSignature": 0,
        "activeParameter": 1,
    }


def test_signature_help_tracks_nested_calls_without_counting_nested_commas() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn target(first: Int, second: Int, third: Int) -> Int { first } "
        "fn nested(left: Int, right: Int) -> Int { left } "
        "fn caller() { target(first, nested(second, third), third) }\n"
    )
    open_nova(server, uri, text)

    outer = signature_help(server, uri, text.rindex("third") + 2)["result"]
    assert outer is not None
    assert outer["activeParameter"] == 2
    assert outer["signatures"][0]["label"] == (
        "fn target(first: Int, second: Int, third: Int) -> Int"
    )

    nested_offset = text.index("second, third") + len("second, th")
    inner = signature_help(server, uri, nested_offset, request_id=3)["result"]
    assert inner is not None
    assert inner["activeParameter"] == 1
    assert inner["signatures"][0]["label"] == (
        "fn nested(left: Int, right: Int) -> Int"
    )


def test_signature_help_does_not_guess_ambiguous_function() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/a.nova", "fn target(value: Int) {}\n")
    open_nova(server, "file:///workspace/b.nova", "fn target(value: Bool) {}\n")
    text = "fn caller() { target(value) }\n"
    open_nova(server, main_uri, text)

    assert signature_help(server, main_uri, text.index("value") + 2)["result"] is None


def test_signature_help_tracks_change_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    text = "fn caller() { target(value) }\n"
    open_nova(server, library_uri, "fn target(value: Int) -> Int { value }\n")
    open_nova(server, main_uri, text)
    position = text.index("value") + 2
    assert signature_help(server, main_uri, position)["result"]["signatures"][0][
        "label"
    ] == "fn target(value: Int) -> Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library_uri, "version": 2},
                "contentChanges": [
                    {"text": "fn target(value: String) -> String { value }\n"}
                ],
            },
        )
    )
    assert signature_help(server, main_uri, position, request_id=3)["result"][
        "signatures"
    ][0]["label"] == "fn target(value: String) -> String"

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": library_uri}})
    )
    assert signature_help(server, main_uri, position, request_id=4)["result"] is None

    open_nova(server, library_uri, "fn target(value: Bool) -> Bool { value }\n")
    assert signature_help(server, main_uri, position, request_id=5)["result"][
        "signatures"
    ][0]["label"] == "fn target(value: Bool) -> Bool"


def test_signature_help_suppresses_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    text = "fn caller() { target(value) }\n"
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
    assert signature_help(server, main_uri, text.index("value") + 2) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_signature_help_honors_cancellation_checkpoint() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn target(value: Int) {} fn caller() { target(value) }\n"
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
    assert signature_help(server, uri, text.rindex("value") + 2) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
