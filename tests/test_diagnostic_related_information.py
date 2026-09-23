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


def test_diagnostic_store_requires_exact_semantic_for_cross_uri_relation() -> None:
    server = initialized_server(related_information=True)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {}\n")
    semantic = server.semantics.get(uri)
    assert semantic is not None

    with pytest.raises(DiagnosticError, match="requires an exact semantic snapshot"):
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


def test_cross_uri_relation_uses_target_document_coordinates() -> None:
    server = initialized_server(related_information=True)
    main_uri = "file:///workspace/main.nova"
    other_uri = "file:///workspace/other.nova"
    open_nova(server, main_uri, "fn main() {}\n")
    open_nova(server, other_uri, "😀\nfn helper() {}\n")

    main = server.semantics.get(main_uri)
    other = server.semantics.get(other_uri)
    assert main is not None
    assert other is not None

    server.drain_notifications()
    assert server.publish_diagnostics(
        main,
        [
            Diagnostic(
                Span(3, 7),
                "example",
                related_information=(
                    DiagnosticRelatedInformation(
                        other_uri,
                        Span(5, 11),
                        "candidate helper is here",
                        semantic=other,
                    ),
                ),
            )
        ],
    )
    notification = server.drain_notifications()[-1]
    related = notification["params"]["diagnostics"][0]["relatedInformation"][0]
    assert related == {
        "location": {
            "uri": other_uri,
            "range": {
                "start": {"line": 1, "character": 3},
                "end": {"line": 1, "character": 9},
            },
        },
        "message": "candidate helper is here",
    }


def test_cross_uri_target_replacement_invalidates_diagnostic_snapshot() -> None:
    server = initialized_server(related_information=True)
    main_uri = "file:///workspace/main.nova"
    other_uri = "file:///workspace/other.nova"
    open_nova(server, main_uri, "fn main() {}\n")
    open_nova(server, other_uri, "fn helper() {}\n")

    main = server.semantics.get(main_uri)
    other = server.semantics.get(other_uri)
    assert main is not None
    assert other is not None
    snapshot = server.diagnostics.publish(
        main,
        [
            Diagnostic(
                Span(3, 7),
                "example",
                related_information=(
                    DiagnosticRelatedInformation(
                        other_uri,
                        Span(3, 9),
                        "candidate helper is here",
                        semantic=other,
                    ),
                ),
            )
        ],
    )
    assert server.diagnostics.get(main_uri) is snapshot

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": other_uri, "version": 2},
                "contentChanges": [{"text": "fn replacement() {}\n"}],
            },
        )
    )

    assert server.diagnostics.get(main_uri) is None
    with pytest.raises(DiagnosticError, match="stale diagnostic snapshot"):
        server.diagnostics.commit_if_current(snapshot, lambda: None)


def test_related_information_rejects_semantic_uri_mismatch() -> None:
    server = initialized_server(related_information=True)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {}\n")
    semantic = server.semantics.get(uri)
    assert semantic is not None

    with pytest.raises(DiagnosticError, match="semantic URI"):
        DiagnosticRelatedInformation(
            "file:///workspace/other.nova",
            Span(3, 7),
            "other",
            semantic=semantic,
        )

def test_ambiguous_workspace_call_relates_all_exact_candidate_definitions() -> None:
    server = initialized_server(related_information=True)
    first_uri = "file:///workspace/a.nova"
    second_uri = "file:///workspace/b.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, first_uri, "fn target() {}\n")
    open_nova(server, second_uri, "😀\nfn target() {}\n")
    server.drain_notifications()

    open_nova(server, caller_uri, "fn main() { target() }\n")
    notifications = [
        item
        for item in server.drain_notifications()
        if item["method"] == "textDocument/publishDiagnostics"
        and item["params"]["uri"] == caller_uri
    ]
    assert notifications
    ambiguous = [
        item
        for item in notifications[-1]["params"]["diagnostics"]
        if item.get("code") == "nova.ambiguous-function"
    ]
    assert len(ambiguous) == 1
    assert ambiguous[0]["relatedInformation"] == [
        {
            "location": {
                "uri": first_uri,
                "range": {
                    "start": {"line": 0, "character": 3},
                    "end": {"line": 0, "character": 9},
                },
            },
            "message": "candidate function declaration 'target' is here",
        },
        {
            "location": {
                "uri": second_uri,
                "range": {
                    "start": {"line": 1, "character": 3},
                    "end": {"line": 1, "character": 9},
                },
            },
            "message": "candidate function declaration 'target' is here",
        },
    ]

    report = pull(server, caller_uri)["result"]
    pulled = [
        item
        for item in report["items"]
        if item.get("code") == "nova.ambiguous-function"
    ]
    assert pulled == ambiguous

    internal = server.diagnostics.get(caller_uri)
    assert internal is not None
    relation_semantics = tuple(
        related.semantic
        for diagnostic in internal.diagnostics
        if diagnostic.code == "nova.ambiguous-function"
        for related in diagnostic.related_information
    )
    assert relation_semantics == (
        server.semantics.get(first_uri),
        server.semantics.get(second_uri),
    )


def test_workspace_ambiguity_relations_rebind_after_provider_change() -> None:
    server = initialized_server(related_information=True)
    first_uri = "file:///workspace/a.nova"
    second_uri = "file:///workspace/b.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, first_uri, "fn target() {}\n")
    open_nova(server, second_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn main() { target() }\n")

    before = server.diagnostics.get(caller_uri)
    assert before is not None
    assert any(
        item.code == "nova.ambiguous-function"
        and len(item.related_information) == 2
        for item in before.diagnostics
    )

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": second_uri, "version": 2},
                "contentChanges": [{"text": "fn other() {}\n"}],
            },
        )
    )

    after = server.diagnostics.get(caller_uri)
    assert after is not None
    assert all(
        item.code != "nova.ambiguous-function"
        for item in after.diagnostics
    )


def test_same_version_target_semantic_replacement_stales_cross_file_relation() -> None:
    server = initialized_server(related_information=True)
    first_uri = "file:///workspace/a.nova"
    second_uri = "file:///workspace/b.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, first_uri, "fn target() {}\n")
    open_nova(server, second_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn main() { target() }\n")

    caller_snapshot = server.diagnostics.get(caller_uri)
    second_document = server.documents.get(second_uri)
    second_workspace = server.workspace_symbols.get(second_uri)
    assert caller_snapshot is not None
    assert second_document is not None
    assert second_workspace is not None

    replacement = server.nova_adapter.publish(server, second_document)
    assert replacement is not second_workspace
    assert replacement.version == second_workspace.version

    assert server.diagnostics.get(caller_uri) is None
    with pytest.raises(DiagnosticError, match="stale diagnostic snapshot"):
        server.diagnostics.commit_if_current(caller_snapshot, lambda: None)

def test_commit_all_if_current_rejects_stale_cross_file_related_parent() -> None:
    server = initialized_server(related_information=True)
    caller_uri = "file:///workspace/main.nova"
    target_uri = "file:///workspace/target.nova"
    open_nova(server, caller_uri, "fn main() {}\n")
    open_nova(server, target_uri, "fn target() {}\n")

    caller = server.semantics.get(caller_uri)
    target = server.semantics.get(target_uri)
    assert caller is not None
    assert target is not None

    snapshot = server.diagnostics.publish(
        caller,
        [
            Diagnostic(
                Span(3, 7),
                "example",
                related_information=(
                    DiagnosticRelatedInformation(
                        target_uri,
                        Span(3, 9),
                        "target is here",
                        semantic=target,
                    ),
                ),
            )
        ],
    )

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": target_uri, "version": 2},
                "contentChanges": [{"text": "fn replacement() {}\n"}],
            },
        )
    )

    committed = False

    def commit() -> None:
        nonlocal committed
        committed = True

    with pytest.raises(DiagnosticError, match="stale diagnostic snapshot set"):
        server.diagnostics.commit_all_if_current((snapshot,), commit)

    assert committed is False
