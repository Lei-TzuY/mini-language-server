from __future__ import annotations

from threading import Event, Thread

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


def publish_semantics(server: LanguageServer, uri: str):
    document = server.documents.get(uri)
    assert document is not None
    syntax = server.syntax.publish(document, tree=object())
    declaration = Symbol("foo", "variable", Span(4, 7))
    symbols = server.symbols.publish(syntax, [declaration])
    return server.semantics.publish(
        symbols, [Reference(Span(12, 15), symbols.symbols[0])]
    )


def test_document_change_suppresses_stale_diagnostic_notification() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "let foo = 1\nfoo\n",
                }
            },
        )
    )
    semantic = publish_semantics(server, uri)

    guard_entered = Event()
    release_guard = Event()
    change_finished = Event()
    errors: list[Exception] = []
    published: list[bool] = []
    original_commit = server.diagnostics.commit_if_current

    def blocking_commit(snapshot, commit):
        guard_entered.set()
        assert release_guard.wait(2)
        return original_commit(snapshot, commit)

    server.diagnostics.commit_if_current = blocking_commit  # type: ignore[method-assign]

    def publish_old_diagnostics() -> None:
        try:
            published.append(
                server.publish_diagnostics(
                    semantic, [Diagnostic(Span(4, 7), "old diagnostic")]
                )
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    publisher = Thread(target=publish_old_diagnostics, name="diagnostic-notification")
    publisher.start()
    assert guard_entered.wait(2)

    def change_document() -> None:
        try:
            server.handle(
                notification(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": uri, "version": 2},
                        "contentChanges": [{"text": "let bar = 1\nbar\n"}],
                    },
                )
            )
            change_finished.set()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    changer = Thread(target=change_document, name="document-change")
    changer.start()
    assert change_finished.wait(2)

    release_guard.set()
    publisher.join(2)
    changer.join(2)

    assert not publisher.is_alive()
    assert not changer.is_alive()
    assert errors == []
    assert published == [False]
    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "version": 2, "diagnostics": []},
        }
    ]
