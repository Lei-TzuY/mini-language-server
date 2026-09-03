from mini_language_server.diagnostics import Diagnostic
from mini_language_server.semantic import Reference
from mini_language_server.server import LanguageServer
from mini_language_server.source import Span
from mini_language_server.symbols import Symbol


def notification(method: str, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialized_server() -> LanguageServer:
    server = LanguageServer()
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    return server


def open_document(server: LanguageServer, uri: str, text: str, version: int = 1) -> None:
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


def publish_semantics(server: LanguageServer, uri: str):
    document = server.documents.get(uri)
    assert document is not None
    syntax = server.syntax.publish(document, tree=object())
    declaration = Symbol("foo", "variable", Span(5, 8))
    symbols = server.symbols.publish(syntax, [declaration])
    return server.semantics.publish(
        symbols, [Reference(Span(13, 16), symbols.symbols[0])]
    )


def test_publish_diagnostics_maps_utf16_and_preserves_deterministic_order() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let 😀foo = 1\nfoo\n")
    semantic = publish_semantics(server, uri)

    assert server.publish_diagnostics(
        semantic,
        [
            Diagnostic(Span(13, 16), "reference warning", "warning", "W001", "nova"),
            Diagnostic(Span(5, 8), "declaration error"),
        ],
    )

    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": uri,
                "version": 1,
                "diagnostics": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 6},
                            "end": {"line": 0, "character": 9},
                        },
                        "severity": 1,
                        "message": "declaration error",
                    },
                    {
                        "range": {
                            "start": {"line": 1, "character": 0},
                            "end": {"line": 1, "character": 3},
                        },
                        "severity": 2,
                        "message": "reference warning",
                        "code": "W001",
                        "source": "nova",
                    },
                ],
            },
        }
    ]
    assert server.drain_notifications() == []


def test_document_change_clears_published_diagnostics_and_suppresses_late_result() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let foo = 1\nfoo\n")
    semantic = publish_semantics(server, uri)
    assert server.publish_diagnostics(semantic, [Diagnostic(Span(5, 8), "old")])
    server.drain_notifications()

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "let bar = 1\nbar\n"}],
            },
        )
    )

    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "version": 2, "diagnostics": []},
        }
    ]
    assert not server.publish_diagnostics(semantic, [Diagnostic(Span(5, 8), "late")])
    assert server.drain_notifications() == []


def test_invalid_or_stale_change_does_not_clear_current_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let foo = 1\nfoo\n", version=3)
    semantic = publish_semantics(server, uri)
    assert server.publish_diagnostics(semantic, [Diagnostic(Span(5, 8), "current")])
    server.drain_notifications()

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 3},
                "contentChanges": [{"text": "stale"}],
            },
        )
    )

    assert server.drain_notifications() == []
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    assert snapshot.diagnostics == (Diagnostic(Span(5, 8), "current"),)


def test_close_clears_client_diagnostics_without_reviving_old_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let foo = 1\nfoo\n")
    semantic = publish_semantics(server, uri)
    assert server.publish_diagnostics(semantic, [Diagnostic(Span(5, 8), "old")])
    server.drain_notifications()

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))

    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "diagnostics": []},
        }
    ]
    assert server.diagnostics.get(uri) is None
    assert not server.publish_diagnostics(semantic, [Diagnostic(Span(5, 8), "late")])
    assert server.drain_notifications() == []
