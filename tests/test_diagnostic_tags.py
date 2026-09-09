from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from mini_language_server import NovaProductLanguageServer, Span
from mini_language_server.diagnostics import Diagnostic, DiagnosticError, DiagnosticSnapshot


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"diagnostic": {}}}},
        )
    )
    assert response is not None
    return server


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
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


def pull(server: NovaProductLanguageServer, uri: str, request_id: int = 2) -> dict:
    response = server.handle(
        request(
            "textDocument/diagnostic",
            request_id,
            {"textDocument": {"uri": uri}},
        )
    )
    assert response is not None
    return response


def tagged_unreachable(items: list[dict[str, Any]]) -> dict[str, Any]:
    return next(item for item in items if item.get("code") == "nova.unreachable-code")


def test_diagnostic_tags_are_validated_as_language_independent_values() -> None:
    diagnostic = Diagnostic(
        Span(0, 1),
        "unused",
        tags=("unnecessary", "deprecated"),
    )
    assert diagnostic.tags == ("unnecessary", "deprecated")

    with pytest.raises(DiagnosticError, match="tuple"):
        Diagnostic(Span(0, 1), "unused", tags=["unnecessary"])  # type: ignore[arg-type]
    with pytest.raises(DiagnosticError, match="unique"):
        Diagnostic(Span(0, 1), "unused", tags=("unnecessary", "unnecessary"))
    with pytest.raises(DiagnosticError, match="unsupported diagnostic tag"):
        Diagnostic(Span(0, 1), "unused", tags=("unknown",))


def test_unreachable_code_is_tagged_unnecessary_in_push_and_pull_protocol() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { return; let value = 1; }\n")

    notifications = server.drain_notifications()
    published = next(
        notification
        for notification in notifications
        if notification["method"] == "textDocument/publishDiagnostics"
        and any(
            item.get("code") == "nova.unreachable-code"
            for item in notification["params"]["diagnostics"]
        )
    )
    assert tagged_unreachable(published["params"]["diagnostics"])["tags"] == [1]

    report = pull(server, uri)["result"]
    assert report["kind"] == "full"
    assert tagged_unreachable(report["items"])["tags"] == [1]


def test_pull_result_id_changes_when_only_diagnostic_tags_change() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { return; let value = 1; }\n")

    document = server.documents.get(uri)
    snapshot = server.diagnostics.get(uri)
    assert document is not None
    assert snapshot is not None
    unreachable = next(
        diagnostic
        for diagnostic in snapshot.diagnostics
        if diagnostic.code == "nova.unreachable-code"
    )
    untagged = DiagnosticSnapshot(
        semantic=snapshot.semantic,
        diagnostics=tuple(
            replace(diagnostic, tags=()) if diagnostic is unreachable else diagnostic
            for diagnostic in snapshot.diagnostics
        ),
    )

    tagged_result_id = server._diagnostic_result_id(document, snapshot)
    untagged_result_id = server._diagnostic_result_id(document, untagged)
    assert tagged_result_id != untagged_result_id


def test_unreachable_tag_tracks_change_close_and_reopen_lifecycle() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { return; let value = 1; }\n", version=1)
    assert tagged_unreachable(pull(server, uri)["result"]["items"])["tags"] == [1]

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { let value = 1; }\n"}],
            },
        )
    )
    assert pull(server, uri, request_id=3)["result"]["items"] == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, "fn main() { return; let value = 2; }\n", version=1)
    reopened = pull(server, uri, request_id=4)["result"]["items"]
    assert tagged_unreachable(reopened)["tags"] == [1]


def test_tagged_pull_rejects_same_version_semantic_replacement(monkeypatch: Any) -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { return; let value = 1; }\n")
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
    assert pull(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
