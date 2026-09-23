from __future__ import annotations

from dataclasses import replace

import pytest

from mini_language_server import NovaProductLanguageServer, Span
from mini_language_server.diagnostics import (
    Diagnostic,
    DiagnosticError,
    DiagnosticRelatedInformation,
    DiagnosticSnapshot,
)


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server(*, related_information: bool) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "diagnostic": {},
                        "publishDiagnostics": {
                            "relatedInformation": related_information,
                        },
                    }
                }
            },
        )
    )
    assert response is not None
    return server


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


def pull(server: NovaProductLanguageServer, uri: str) -> dict:
    response = server.handle(
        request(
            "textDocument/diagnostic",
            2,
            {"textDocument": {"uri": uri}},
        )
    )
    assert response is not None
    return response


def duplicate_items(items: list[dict]) -> list[dict]:
    return [
        item
        for item in items
        if item.get("code") == "nova.duplicate-function"
    ]


def test_duplicate_functions_render_related_information_in_push_and_pull() -> None:
    server = initialized_server(related_information=True)
    uri = "file:///workspace/main.nova"
    text = "fn foo() {}\nfn foo() {}\n"
    open_nova(server, uri, text)

    published = next(
        item
        for item in server.drain_notifications()
        if item["method"] == "textDocument/publishDiagnostics"
    )
    push = duplicate_items(published["params"]["diagnostics"])
    assert len(push) == 2
    assert push[0]["relatedInformation"] == [
        {
            "location": {
                "uri": uri,
                "range": {
                    "start": {"line": 1, "character": 3},
                    "end": {"line": 1, "character": 6},
                },
            },
            "message": "conflicting function declaration 'foo' is here",
        }
    ]
    assert push[1]["relatedInformation"] == [
        {
            "location": {
                "uri": uri,
                "range": {
                    "start": {"line": 0, "character": 3},
                    "end": {"line": 0, "character": 6},
                },
            },
            "message": "conflicting function declaration 'foo' is here",
        }
    ]

    report = pull(server, uri)["result"]
    assert report["kind"] == "full"
    assert duplicate_items(report["items"]) == push


def test_related_information_is_protocol_optional_but_semantically_preserved() -> None:
    server = initialized_server(related_information=False)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn foo() {}\nfn foo() {}\n")

    published = next(
        item
        for item in server.drain_notifications()
        if item["method"] == "textDocument/publishDiagnostics"
    )
    push = duplicate_items(published["params"]["diagnostics"])
    assert len(push) == 2
    assert all("relatedInformation" not in item for item in push)

    report = pull(server, uri)["result"]
    assert all(
        "relatedInformation" not in item
        for item in duplicate_items(report["items"])
    )

    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    internal = [
        item
        for item in snapshot.diagnostics
        if item.code == "nova.duplicate-function"
    ]
    assert len(internal) == 2
    assert all(len(item.related_information) == 1 for item in internal)


def test_duplicate_function_parameter_and_local_all_carry_related_locations() -> None:
    server = initialized_server(related_information=False)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn foo(value, value) { let item = 1 let item = 2 }\n"
            "fn foo() {}\n"
        ),
    )

    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    codes = {
        "nova.duplicate-function",
        "nova.duplicate-parameter",
        "nova.duplicate-variable",
    }
    duplicates = [item for item in snapshot.diagnostics if item.code in codes]
    assert len(duplicates) == 6
    assert all(len(item.related_information) == 1 for item in duplicates)
    assert all(
        item.related_information[0].uri == uri
        for item in duplicates
    )


def test_pull_result_id_changes_when_only_related_information_changes() -> None:
    server = initialized_server(related_information=False)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn foo() {}\nfn foo() {}\n")

    document = server.documents.get(uri)
    snapshot = server.diagnostics.get(uri)
    assert document is not None
    assert snapshot is not None

    stripped = DiagnosticSnapshot(
        semantic=snapshot.semantic,
        diagnostics=tuple(
            replace(item, related_information=())
            if item.code == "nova.duplicate-function"
            else item
            for item in snapshot.diagnostics
        ),
    )
    assert server._diagnostic_result_id(
        document, snapshot
    ) != server._diagnostic_result_id(document, stripped)


def test_related_information_model_rejects_invalid_values() -> None:
    with pytest.raises(DiagnosticError, match="URI"):
        DiagnosticRelatedInformation("", Span(0, 1), "other")
    with pytest.raises(DiagnosticError, match="message"):
        DiagnosticRelatedInformation("file:///a.nova", Span(0, 1), "")

    related = DiagnosticRelatedInformation(
        "file:///a.nova", Span(0, 1), "other"
    )
    with pytest.raises(DiagnosticError, match="tuple"):
        Diagnostic(
            Span(0, 1),
            "duplicate",
            related_information=[related],  # type: ignore[arg-type]
        )
    with pytest.raises(DiagnosticError, match="unique"):
        Diagnostic(
            Span(0, 1),
            "duplicate",
            related_information=(related, related),
        )


def test_diagnostic_store_rejects_cross_uri_related_information() -> None:
    server = initialized_server(related_information=True)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {}\n")
    semantic = server.semantics.get(uri)
    assert semantic is not None

    with pytest.raises(DiagnosticError, match="same semantic URI"):
        server.diagnostics.publish(
            semantic,
            [
                Diagnostic(
                    Span(3, 7),
                    "example",
                    related_information=(
                        DiagnosticRelatedInformation(
                            "file:///workspace/other.nova",
                            Span(0, 1),
                            "other",
                        ),
                    ),
                )
            ],
        )
