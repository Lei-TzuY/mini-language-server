from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"signatureHelp": {}}}},
        )
    )
    assert result is not None


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


def signature_label(
    server: NovaProductLanguageServer,
    uri: str,
    character: int,
    *,
    request_id: int = 2,
) -> str | None:
    response = server.handle(
        request(
            "textDocument/signatureHelp",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": character},
            },
        )
    )
    assert response is not None
    result = response.get("result")
    if result is None:
        return None
    return result["signatures"][0]["label"]


def test_signature_help_surfaces_cross_file_inferred_return_type() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/library.nova", "fn target(value: Int) { return 1; }\n")
    text = "fn caller() { target(value) }\n"
    open_nova(server, main_uri, text)

    assert signature_label(server, main_uri, text.index("value") + 2) == (
        "fn target(value: Int) -> Int"
    )


def test_signature_help_preserves_explicit_and_ambiguous_results() -> None:
    explicit = NovaProductLanguageServer()
    initialize(explicit)
    main_uri = "file:///workspace/main.nova"
    open_nova(
        explicit,
        "file:///workspace/library.nova",
        'fn target(value: Int) -> String { return "x"; }\n',
    )
    text = "fn caller() { target(value) }\n"
    open_nova(explicit, main_uri, text)
    assert signature_label(explicit, main_uri, text.index("value") + 2) == (
        "fn target(value: Int) -> String"
    )

    ambiguous = NovaProductLanguageServer()
    initialize(ambiguous)
    open_nova(ambiguous, "file:///workspace/a.nova", "fn target(value: Int) { return 1; }\n")
    open_nova(ambiguous, "file:///workspace/b.nova", "fn target(value: Int) { return 1; }\n")
    open_nova(ambiguous, main_uri, text)
    assert signature_label(ambiguous, main_uri, text.index("value") + 2) is None


def test_inferred_signature_help_recomputes_across_change_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    text = "fn caller() { target(value) }\n"
    open_nova(server, library_uri, "fn target(value: Int) { return 1; }\n")
    open_nova(server, main_uri, text)
    position = text.index("value") + 2
    assert signature_label(server, main_uri, position) == "fn target(value: Int) -> Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library_uri, "version": 2},
                "contentChanges": [
                    {"text": 'fn target(value: Int) { return "x"; }\n'}
                ],
            },
        )
    )
    assert signature_label(server, main_uri, position, request_id=3) == (
        "fn target(value: Int) -> String"
    )

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": library_uri}})
    )
    assert signature_label(server, main_uri, position, request_id=4) is None

    open_nova(server, library_uri, "fn target(value: Int) { return true; }\n")
    assert signature_label(server, main_uri, position, request_id=5) == (
        "fn target(value: Int) -> Bool"
    )


def test_inferred_signature_help_suppresses_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    text = "fn caller() { target(value) }\n"
    open_nova(server, library_uri, "fn target(value: Int) { return 1; }\n")
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
    response = server.handle(
        request(
            "textDocument/signatureHelp",
            2,
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 0, "character": text.index("value") + 2},
            },
        )
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_inferred_signature_help_honors_cancellation_checkpoint() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn target(value: Int) { return 1; } fn caller() { target(value) }\n"
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
    response = server.handle(
        request(
            "textDocument/signatureHelp",
            2,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": text.rindex("value") + 2},
            },
        )
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
